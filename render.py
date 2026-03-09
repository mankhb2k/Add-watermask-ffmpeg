#!/usr/bin/env python3
"""
Script render video trong uploads, xuất ra outputs.
Cùng thông số và logic như app.py, không qua Flask/HTML.
Chạy: python render.py [--logo PATH] [--output-dir PATH] [--ratio 9:16|16:9|original] ...
"""
import os
import sys
import argparse
import subprocess
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed

UPLOAD_FOLDER = "uploads"
DEFAULT_OUTPUT_FOLDER = "outputs"
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".flv", ".wmv"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def _render_one(args):
    """Worker (đa luồng): xử lý 1 video, capture output để không lẫn khi chạy song song."""
    input_path, output_name, logo_path, filter_complex, output_dir = args
    output_path = os.path.join(output_dir, output_name)
    cmd = [
        "ffmpeg", "-y", "-i", input_path, "-i", logo_path,
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-crf", "18", "-preset", "slower", "-c:a", "copy", output_path
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return (output_name, True, None)
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b"").decode("utf-8", errors="replace") if e.stderr else str(e)
        return (output_name, False, stderr)


def _run_ffmpeg_sequential(args, index, total, verbose):
    """Chạy 1 video tuần tự, in tiến trình FFmpeg ra terminal (dùng trên VPS để xem lỗi)."""
    input_path, output_name, logo_path, filter_complex, output_dir = args
    output_path = os.path.join(output_dir, output_name)
    cmd = [
        "ffmpeg", "-y", "-i", input_path, "-i", logo_path,
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-crf", "18", "-preset", "slower", "-c:a", "copy", output_path
    ]
    if verbose:
        print(f"\n--- Lệnh FFmpeg ({index}/{total}) {output_name} ---")
        print(" ".join(cmd[:4]) + " -filter_complex '...' " + " ".join(cmd[-3:]))
    print(f"\n[{index}/{total}] Đang xử lý: {output_name}", flush=True)
    try:
        # Không capture để FFmpeg in frame=... fps=... ra terminal
        subprocess.run(cmd, check=True)
        print(f"[{index}/{total}] Xong: {output_name}", flush=True)
        return (output_name, True, None)
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b"").decode("utf-8", errors="replace") if e.stderr else str(e)
        print(f"\n[{index}/{total}] LỖI: {output_name}", file=sys.stderr)
        print("--- FFmpeg stderr ---", file=sys.stderr)
        print(stderr, file=sys.stderr)
        print("---", file=sys.stderr)
        return (output_name, False, stderr)


def find_logo_in_uploads():
    """Tìm logo.png hoặc logo.jpg trong uploads."""
    if not os.path.isdir(UPLOAD_FOLDER):
        return None
    for name in ("logo.png", "logo.jpg"):
        path = os.path.join(UPLOAD_FOLDER, name)
        if os.path.isfile(path):
            return path
    return None


def list_videos(folder):
    """Liệt kê (full_path, basename) các file video trong folder."""
    entries = []
    for f in os.listdir(folder):
        ext = os.path.splitext(f)[1].lower()
        if ext in VIDEO_EXTENSIONS:
            full = os.path.join(folder, f)
            if os.path.isfile(full):
                entries.append((full, f))
    return sorted(entries, key=lambda x: x[1])


def build_filter_complex(video_ratio, logo_scale_pct, logo_bottom_pct, logo_opacity):
    """Tạo filter_complex chuẩn, đảm bảo các nhãn luôn tồn tại."""
    # Bước 1: Scale video theo tỷ lệ
    if video_ratio == "16:9":
        video_part = "[0:v]scale=1280:-2:force_original_aspect_ratio=decrease,pad=1280:720:(1280-iw)/2:(720-ih)/2[base];"
    elif video_ratio == "9:16":
        video_part = "[0:v]scale=-2:960:force_original_aspect_ratio=decrease,pad=540:960:(540-iw)/2:(960-ih)/2[base];"
    else:
        # Quan trọng: Phải dùng [0:v] thay vì [0] để chỉ định rõ luồng video
        video_part = "[0:v]null[base];"

    # Bước 2: Xử lý Logo và Overlay
    # Dùng scale2ref để logo tự co giãn theo video đã scale (nhãn [base])
    filter_string = (
        video_part +
        f"[1:v]format=rgba,colorchannelmixer=aa={logo_opacity:.4f}[logo_trans];" +
        f"[logo_trans][base]scale2ref=w=oh*mdar:h=ih*{logo_scale_pct:.4f}[logo_scaled][video_ref];" +
        f"[logo_scaled]scale=trunc(iw/2)*2:trunc(ih/2)*2[logo_final];" +
        f"[video_ref][logo_final]overlay=(main_w-overlay_w)/2:main_h-overlay_h-main_h*{logo_bottom_pct:.2f}/100:enable='between(t,0,10)'[v]"
    )
    return filter_string


def main():
    parser = argparse.ArgumentParser(description="Render video trong uploads ra outputs (cùng thông số app.py)")
    parser.add_argument("--logo", "-l", help="Đường dẫn file logo (mặc định: tìm logo.png hoặc logo.jpg trong uploads)")
    parser.add_argument("--output-dir", "-o", default=DEFAULT_OUTPUT_FOLDER, help="Thư mục xuất (mặc định: outputs)")
    parser.add_argument("--ratio", "-r", choices=["9:16", "16:9", "original"], default="9:16", help="Tỷ lệ video (mặc định: 9:16)")
    parser.add_argument("--logo-scale", type=float, default=5, help="Kích thước logo %% chiều cao video (2-15, mặc định: 5)")
    parser.add_argument("--logo-bottom", type=float, default=30, help="Vị trí logo %% từ dưới lên (0-60, mặc định: 10)")
    parser.add_argument("--logo-opacity", type=float, default=30, help="Độ mờ logo %% (0-100, mặc định: 50)")
    parser.add_argument("--workers", "-w", type=int, default=None, help="Số luồng (mặc định: min(cpu_count, số video, 8))")
    parser.add_argument("--sequential", "-s", action="store_true", help="Chạy từng video một, in tiến trình FFmpeg ra terminal (để debug trên VPS)")
    parser.add_argument("--verbose", "-v", action="store_true", help="In lệnh FFmpeg khi chạy; khi lỗi in đầy đủ stderr")
    parser.add_argument("--dry-run", action="store_true", help="Chỉ in danh sách video, không render")
    args = parser.parse_args()

    uploads = os.path.abspath(UPLOAD_FOLDER)
    if not os.path.isdir(uploads):
        print(f"Lỗi: không tìm thấy thư mục '{UPLOAD_FOLDER}'", file=sys.stderr)
        sys.exit(1)

    logo_path = args.logo
    if not logo_path:
        logo_path = find_logo_in_uploads()
    if not logo_path or not os.path.isfile(logo_path):
        print("Lỗi: không tìm thấy logo.png hoặc logo.jpg trong uploads. Đặt file hoặc dùng --logo PATH", file=sys.stderr)
        sys.exit(1)
    logo_path = os.path.abspath(logo_path)

    video_entries = list_videos(uploads)
    if not video_entries:
        print(f"Không có file video trong '{UPLOAD_FOLDER}'", file=sys.stderr)
        sys.exit(1)

    output_dir = os.path.abspath(args.output_dir)
    if output_dir == uploads or output_dir.startswith(uploads + os.sep):
        print("Lỗi: thư mục output không được trùng hoặc nằm trong uploads", file=sys.stderr)
        sys.exit(1)
    os.makedirs(output_dir, exist_ok=True)

    # Thông số giống app.py
    logo_scale_pct = max(2, min(15, args.logo_scale)) / 100.0
    logo_bottom_pct = max(0, min(60, args.logo_bottom))
    logo_opacity = max(0, min(100, args.logo_opacity)) / 100.0

    filter_complex = build_filter_complex(args.ratio, logo_scale_pct, logo_bottom_pct, logo_opacity)

    total = len(video_entries)
    n_workers = args.workers
    if n_workers is None:
        n_workers = min(multiprocessing.cpu_count(), total, 8) or 1
    n_workers = max(1, min(n_workers, total))

    print(f"Logo: {logo_path}")
    print(f"Output: {output_dir}")
    print(f"Video: {total} file | Workers: {n_workers}")
    print(f"Thông số: ratio={args.ratio}, logo_scale={args.logo_scale}%%, logo_bottom={args.logo_bottom}%%, opacity={args.logo_opacity}%%")
    if args.dry_run:
        for _, name in video_entries:
            print(f"  - {name}")
        return

    task_args = [
        (input_path, output_name, logo_path, filter_complex, output_dir)
        for input_path, output_name in video_entries
    ]
    results = []

    if args.sequential:
        # Chạy từng video một, FFmpeg in tiến trình ra terminal → dễ xem lỗi trên VPS
        for i, t in enumerate(task_args, 1):
            out_name, ok, err = _run_ffmpeg_sequential(t, i, total, args.verbose)
            if ok:
                results.append(out_name)
        print(f"\nXong. Thành công: {len(results)}/{total}. Output: {output_dir}")
        return

    # Đa luồng: in tiến trình và khi lỗi in đầy đủ stderr
    done = 0
    with ProcessPoolExecutor(max_workers=n_workers) as executor:
        future_to_name = {executor.submit(_render_one, a): a[1] for a in task_args}
        for future in as_completed(future_to_name):
            name = future_to_name[future]
            try:
                out_name, ok, err = future.result()
                done += 1
                if ok:
                    results.append(out_name)
                    print(f"[{done}/{total}] OK: {out_name}", flush=True)
                else:
                    print(f"\n[{done}/{total}] LỖI: {out_name}", file=sys.stderr, flush=True)
                    print("--- FFmpeg stderr ---", file=sys.stderr)
                    print(err or "FFmpeg error", file=sys.stderr)
                    print("---", file=sys.stderr)
            except Exception as e:
                done += 1
                print(f"[{done}/{total}] Lỗi ngoại lệ: {name} — {e}", file=sys.stderr, flush=True)

    print(f"\nXong. Thành công: {len(results)}/{total}. Output: {output_dir}")


if __name__ == "__main__":
    main()
