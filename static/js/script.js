let pickedDirHandle = null;

function supportFolderPicker() {
  return "showDirectoryPicker" in window;
}

async function pickOutputFolder() {
  if (!supportFolderPicker()) {
    alert("Trình duyệt của bạn không hỗ trợ chọn thư mục (dùng Chrome/Edge). Bạn có thể nhập đường dẫn vào ô bên trên.");
    return;
  }
  try {
    const handle = await window.showDirectoryPicker();
    pickedDirHandle = handle;
    document.getElementById("picked_folder_hint").textContent = "Đã chọn: " + handle.name;
    document.getElementById("output_dir").value = "";
  } catch (e) {
    if (e.name !== "AbortError") {
      alert("Không thể chọn thư mục: " + (e.message || e));
    }
    pickedDirHandle = null;
    document.getElementById("picked_folder_hint").textContent = "";
  }
}

async function saveFilesToPickedFolder(filenames) {
  if (!pickedDirHandle || !filenames || !filenames.length) return;
  const status = document.getElementById("status");
  status.className = "processing";
  status.innerText = "Đang lưu " + filenames.length + " file vào thư mục đã chọn...";
  try {
    for (const name of filenames) {
      const res = await fetch("/download/" + encodeURIComponent(name));
      if (!res.ok) throw new Error("Tải " + name + " thất bại");
      const blob = await res.blob();
      const fileHandle = await pickedDirHandle.getFileHandle(name, { create: true });
      const writable = await fileHandle.createWritable();
      await writable.write(blob);
      await writable.close();
    }
    status.className = "success";
    status.innerText = "Xử lý xong! Đã lưu " + filenames.length + " file vào thư mục đã chọn.";
  } catch (e) {
    status.className = "error";
    status.innerText = "Lỗi khi lưu file: " + (e.message || e);
  }
}

async function startProcessing() {
  const isFolderMode = document.querySelector('input[name="input_mode"]:checked').value === "folder";
  const outputDirInput = document.getElementById("output_dir").value.trim();
  const btn = document.getElementById("btn");
  const status = document.getElementById("status");

  if (isFolderMode) {
    const folderVideos = document.getElementById("folder_videos").value.trim();
    const folderLogo = document.getElementById("folder_logo").value.trim();
    if (!folderVideos || !folderLogo) {
      alert("Chế độ folder: nhập đủ đường dẫn folder video và file logo.");
      return;
    }
  } else {
    const logo = document.getElementById("logo").files[0];
    const videos = document.getElementById("videos").files;
    if (!logo || videos.length === 0) {
      alert("Vui lòng chọn đủ logo và ít nhất 1 video!");
      return;
    }
  }

  const formData = new FormData();
  formData.append("use_folder", isFolderMode ? "1" : "0");
  formData.append("video_ratio", document.getElementById("video_ratio").value);
  formData.append("logo_scale", document.getElementById("logo_scale").value || "5");
  formData.append("logo_bottom_pct", document.getElementById("logo_bottom_pct").value || "10");
  formData.append("logo_opacity", document.getElementById("logo_opacity").value || "50");
  const useTypedPath = outputDirInput.length > 0;
  formData.append("use_picker", !useTypedPath && pickedDirHandle ? "1" : "0");
  formData.append("output_dir", outputDirInput);

  if (isFolderMode) {
    formData.append("folder_videos", document.getElementById("folder_videos").value.trim());
    formData.append("folder_logo", document.getElementById("folder_logo").value.trim());
  } else {
    formData.append("logo", document.getElementById("logo").files[0]);
    const videos = document.getElementById("videos").files;
    for (let i = 0; i < videos.length; i++) {
      formData.append("videos", videos[i]);
    }
  }

  btn.disabled = true;
  status.className = "processing";
  status.innerText = "Đang xử lý...";

  const progressWrap = document.getElementById("progress_wrap");
  const progressFill = document.getElementById("progress_fill");
  const progressText = document.getElementById("progress_text");
  progressWrap.style.display = "none";
  progressFill.style.width = "0%";

  try {
    const response = await fetch("/process", {
      method: "POST",
      body: formData,
    });

    if (!response.ok) {
      const result = await response.json();
      status.className = "error";
      status.innerText = result.error || "Có lỗi xảy ra!";
      btn.disabled = false;
      return;
    }

    progressWrap.style.display = "block";
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let lastResult = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        try {
          const obj = JSON.parse(trimmed);
          if (obj.type === "progress") {
            const pct = obj.total ? (obj.current / obj.total) * 100 : 0;
            progressFill.style.width = pct + "%";
            if (obj.rendering) {
              if (obj.parallel && obj.current === 0) {
                progressText.textContent = "Đang xử lý song song (" + (obj.parallel || 1) + " luồng) — 0 / " + obj.total;
                status.innerText = "Đang xử lý song song " + obj.total + " video...";
              } else {
                progressText.textContent = "Đang render " + (obj.current + 1) + "/" + obj.total + " — " + (obj.file || "");
                status.innerText = "Đang render: " + (obj.file || "");
              }
            } else {
              progressText.textContent = obj.current + " / " + obj.total + (obj.file ? " — " + obj.file + " xong" : "");
              status.innerText = "Đã xong " + obj.current + "/" + obj.total + " video.";
            }
          } else if (obj.type === "done") {
            lastResult = obj;
          } else if (obj.type === "error") {
            status.className = "error";
            status.innerText = "Lỗi " + (obj.file || "") + ": " + (obj.message || "");
          }
        } catch (_) {}
      }
    }
    if (buffer.trim()) {
      try {
        const obj = JSON.parse(buffer.trim());
        if (obj.type === "done") lastResult = obj;
      } catch (_) {}
    }

    if (lastResult) {
      progressFill.style.width = "100%";
      progressText.textContent = lastResult.files.length + " / " + lastResult.files.length;
      status.className = "success";
      if (pickedDirHandle && lastResult.files && lastResult.files.length > 0) {
        await saveFilesToPickedFolder(lastResult.files);
      } else {
        status.innerText =
          lastResult.message + (lastResult.output_folder ? " Thư mục: " + lastResult.output_folder : "");
      }
    }
  } catch (error) {
    status.className = "error";
    status.innerText = "Có lỗi xảy ra!";
  } finally {
    btn.disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", function () {
  const uploadBlock = document.getElementById("upload_block");
  const folderBlock = document.getElementById("folder_block");
  document.querySelectorAll('input[name="input_mode"]').forEach(function (radio) {
    radio.addEventListener("change", function () {
      const isFolder = this.value === "folder";
      uploadBlock.style.display = isFolder ? "none" : "block";
      folderBlock.style.display = isFolder ? "block" : "none";
    });
  });

  const btnPick = document.getElementById("btn_pick_folder");
  if (btnPick) {
    btnPick.addEventListener("click", pickOutputFolder);
  }
  const outputDirInput = document.getElementById("output_dir");
  if (outputDirInput) {
    outputDirInput.addEventListener("input", function () {
      if (pickedDirHandle) {
        pickedDirHandle = null;
        document.getElementById("picked_folder_hint").textContent = "";
      }
    });
  }
  if (!supportFolderPicker()) {
    document.getElementById("picked_folder_hint").textContent =
      "Trình duyệt không hỗ trợ chọn thư mục. Dùng ô nhập đường dẫn.";
  }
});
