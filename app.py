import os
import subprocess
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed
from flask import Flask, render_template, request, jsonify, send_from_directory, Response, stream_with_context
from werkzeug.utils import secure_filename
import json


def _render_one(args):
    """Worker cho multiprocessing: xử lý 1 video, trả về (output_name, success, error_msg)."""
    input_path, output_name, logo_path, filter_complex, output_dir = args
    output_path = os.path.join(output_dir, output_name)
    cmd = [
        'ffmpeg', '-y', '-i', input_path, '-i', logo_path,
        '-filter_complex', filter_complex,
        '-map', '[v]', '-map', '0:a?',
        '-c:v', 'libx264', '-crf', '18', '-preset', 'slower', '-c:a', 'copy', output_path
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return (output_name, True, None)
    except subprocess.CalledProcessError as e:
        return (output_name, False, str(e))

app = Flask(__name__)

# Cấu hình thư mục
UPLOAD_FOLDER = 'uploads'
DEFAULT_OUTPUT_FOLDER = 'outputs'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(DEFAULT_OUTPUT_FOLDER, exist_ok=True)

def get_output_folder(raw_path):
    """Chọn thư mục output, kiểm tra không trùng với thư mục upload."""
    if not raw_path or not str(raw_path).strip():
        return os.path.abspath(DEFAULT_OUTPUT_FOLDER)
    path = os.path.normpath(str(raw_path).strip())
    if not os.path.isabs(path):
        path = os.path.abspath(path)
    upload_abs = os.path.abspath(UPLOAD_FOLDER)
    if path == upload_abs or path.startswith(upload_abs + os.sep):
        return None  # không cho lưu trùng/trong thư mục input
    return path

@app.route('/')
def index():
    return render_template('index.html')

VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.webm', '.m4v', '.flv', '.wmv'}


@app.route('/process', methods=['POST'])
def process():
    use_folder_mode = request.form.get('use_folder') == '1'

    if use_folder_mode:
        folder_videos = (request.form.get('folder_videos') or '').strip()
        folder_logo = (request.form.get('folder_logo') or '').strip()
        if not folder_videos or not folder_logo:
            return jsonify({"error": "Chế độ folder: nhập đủ đường dẫn folder video và file logo."}), 400
        folder_videos = os.path.normpath(os.path.abspath(folder_videos))
        folder_logo = os.path.normpath(os.path.abspath(folder_logo))
        if not os.path.isdir(folder_videos):
            return jsonify({"error": "Folder chứa video không tồn tại hoặc không phải thư mục."}), 400
        if not os.path.isfile(folder_logo):
            return jsonify({"error": "File logo không tồn tại."}), 400
        logo_path = folder_logo
        video_entries = []
        for f in os.listdir(folder_videos):
            base, ext = os.path.splitext(f)
            if ext.lower() in VIDEO_EXTENSIONS:
                full = os.path.join(folder_videos, f)
                if os.path.isfile(full):
                    video_entries.append((full, f))
        if not video_entries:
            return jsonify({"error": "Folder video không chứa file video (mp4, mkv, ...)."}), 400
    else:
        if 'logo' not in request.files or 'videos' not in request.files:
            return jsonify({"error": "Thiếu file logo hoặc video"}), 400
        logo_file = request.files['logo']
        video_files = request.files.getlist('videos')
        logo_path = os.path.join(UPLOAD_FOLDER, secure_filename(logo_file.filename))
        logo_file.save(logo_path)
        video_entries = []
        for v in video_files:
            name = secure_filename(v.filename)
            path = os.path.join(UPLOAD_FOLDER, name)
            v.save(path)
            video_entries.append((path, name))

    raw_output_dir = (request.form.get('output_dir') or '').strip()
    use_picker = request.form.get('use_picker') == '1' and not raw_output_dir
    if use_picker:
        output_dir = os.path.abspath(DEFAULT_OUTPUT_FOLDER)
    else:
        output_dir = get_output_folder(raw_output_dir)

    if output_dir is None:
        return jsonify({
            "error": "Thư mục lưu không được trùng với thư mục chứa video gốc. Chọn thư mục khác."
        }), 400
    if use_folder_mode and (output_dir == folder_videos or output_dir.startswith(folder_videos + os.sep)):
        return jsonify({"error": "Thư mục lưu không được trùng hoặc nằm trong folder video nguồn."}), 400

    os.makedirs(output_dir, exist_ok=True)

    results = []
    video_ratio = request.form.get("video_ratio") or "9:16"
    try:
        logo_scale_pct = float(request.form.get("logo_scale") or "5")
    except (TypeError, ValueError):
        logo_scale_pct = 5
    logo_scale_pct = max(2, min(15, logo_scale_pct)) / 100.0  # chỉ cho scale nhỏ, tối đa 15%
    try:
        logo_bottom_pct = float(request.form.get("logo_bottom_pct") or "10")
    except (TypeError, ValueError):
        logo_bottom_pct = 30
    logo_bottom_pct = max(0, min(60, logo_bottom_pct))  # % từ mép dưới lên (0 = sát đáy, 40 = vùng giữa-dưới)
    try:
        logo_opacity_pct = float(request.form.get("logo_opacity") or "50")
    except (TypeError, ValueError):
        logo_opacity_pct = 30
    logo_opacity = max(0, min(100, logo_opacity_pct)) / 100.0  # 0-100% -> 0-1 cho colorchannelmixer aa

    # Phần scale/pad video theo tỷ lệ 9:16 hoặc 16:9 (hoặc giữ nguyên)
    if video_ratio == "16:9":
        video_part = "[0]scale=1280:-2:force_original_aspect_ratio=decrease,pad=1280:720:(1280-iw)/2:(720-ih)/2[video_scaled];"
    elif video_ratio == "9:16":
        video_part = "[0]scale=-2:960:force_original_aspect_ratio=decrease,pad=540:960:(540-iw)/2:(960-ih)/2[video_scaled];"
    else:
        video_part = "[0]scale=iw:ih[video_scaled];"

    # Logo: scale theo % chiều cao video, ép chẵn. Overlay chính giữa ngang, vị trí dọc theo % từ dưới lên.
    # overlay y = main_h - overlay_h - main_h*pct/100 (pct = % từ mép dưới, 0 = sát đáy).
    filter_complex = (
        video_part
        + f"[1]format=rgba,colorchannelmixer=aa={logo_opacity:.4f}[logo_trans];"
        + f"[logo_trans][video_scaled]scale2ref=w=oh*mdar:h=ih*{logo_scale_pct:.4f}[logo0][_];"
        + "[_]null;"
        + "[logo0]scale=trunc(iw/2)*2:trunc(ih/2)*2[logo];"
        + f"[video_scaled][logo]overlay=(main_w-overlay_w)/2:main_h-overlay_h-main_h*{logo_bottom_pct:.2f}/100:enable='between(t,0,10)'[v]"
    )

    total = len(video_entries)
    n_workers = min(multiprocessing.cpu_count(), total, 8) or 1
    task_args = [
        (input_path, output_name, logo_path, filter_complex, output_dir)
        for input_path, output_name in video_entries
    ]

    def generate():
        yield json.dumps({"type": "progress", "current": 0, "total": total, "file": "", "rendering": True, "parallel": n_workers}) + "\n"
        with ProcessPoolExecutor(max_workers=n_workers) as executor:
            future_to_name = {executor.submit(_render_one, args): args[1] for args in task_args}
            for future in as_completed(future_to_name):
                output_name = future_to_name[future]
                try:
                    name, ok, err = future.result()
                    if ok:
                        results.append(name)
                    yield json.dumps({"type": "progress", "current": len(results), "total": total, "file": name}) + "\n"
                    if not ok:
                        print(f"Lỗi khi xử lý {name}: {err}")
                        yield json.dumps({"type": "error", "file": name, "message": err or "Lỗi FFmpeg"}) + "\n"
                except Exception as e:
                    yield json.dumps({"type": "error", "file": output_name, "message": str(e)}) + "\n"

        yield json.dumps({
            "type": "done",
            "message": "Xử lý hoàn tất!",
            "files": results,
            "output_folder": output_dir
        }) + "\n"

    return Response(
        stream_with_context(generate()),
        mimetype="application/x-ndjson",
        headers={"X-Accel-Buffering": "no"}
    )


@app.route('/download/<path:subpath>')
def download(subpath):
    """Phục vụ file output để client lưu vào thư mục đã chọn (chỉ từ thư mục outputs)."""
    name = secure_filename(os.path.basename(subpath))
    if not name or name != os.path.basename(subpath):
        return jsonify({"error": "Tên file không hợp lệ"}), 400
    path = os.path.join(DEFAULT_OUTPUT_FOLDER, name)
    if not os.path.isfile(path):
        return jsonify({"error": "File không tồn tại"}), 404
    return send_from_directory(DEFAULT_OUTPUT_FOLDER, name, as_attachment=True, download_name=name)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
