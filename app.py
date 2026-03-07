import os
import subprocess
from flask import Flask, render_template, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename

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

@app.route('/process', methods=['POST'])
def process():
    if 'logo' not in request.files or 'videos' not in request.files:
        return jsonify({"error": "Thiếu file logo hoặc video"}), 400

    logo_file = request.files['logo']
    video_files = request.files.getlist('videos')
    raw_output_dir = (request.form.get('output_dir') or '').strip()
    use_picker = request.form.get('use_picker') == '1' and not raw_output_dir
    if use_picker:
        output_dir = os.path.abspath(DEFAULT_OUTPUT_FOLDER)
    else:
        output_dir = get_output_folder(raw_output_dir)

    if output_dir is None:
        return jsonify({
            "error": "Thư mục lưu không được trùng với thư mục chứa video gốc (uploads). Chọn thư mục khác."
        }), 400

    os.makedirs(output_dir, exist_ok=True)

    # Lưu logo
    logo_path = os.path.join(UPLOAD_FOLDER, secure_filename(logo_file.filename))
    logo_file.save(logo_path)

    results = []

    # Filter: scale LOGO theo VIDEO (input 1 = scaled, input 2 = reference). Ép logo chẵn để libx264 không lỗi.
    filter_complex = (
        "[1]format=rgba,colorchannelmixer=aa=0.5[logo_trans];"
        "[logo_trans][0]scale2ref=w=oh*mdar:h=ih*0.1[logo0][video];"
        "[logo0]scale=trunc(iw/2)*2:trunc(ih/2)*2[logo];"
        "[video][logo]overlay=(main_w-overlay_w)/2:main_h/3:enable='between(t,0,12)'"
    )

    for video in video_files:
        video_name = secure_filename(video.filename)
        input_path = os.path.join(UPLOAD_FOLDER, video_name)
        output_name = video_name  # tên giống đầu vào
        output_path = os.path.join(output_dir, output_name)

        video.save(input_path)

        cmd = [
            'ffmpeg', '-y', '-i', input_path, '-i', logo_path,
            '-filter_complex', filter_complex,
            '-c:v', 'libx264', '-crf', '18', '-c:a', 'copy', output_path
        ]

        try:
            subprocess.run(cmd, check=True)
            results.append(output_name)
        except subprocess.CalledProcessError as e:
            print(f"Lỗi khi xử lý {video_name}: {e}")

    return jsonify({
        "message": "Xử lý hoàn tất!",
        "files": results,
        "output_folder": output_dir
    })


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
    app.run(debug=True, port=5000)
