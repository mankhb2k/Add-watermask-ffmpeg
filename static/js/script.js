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
  const logo = document.getElementById("logo").files[0];
  const videos = document.getElementById("videos").files;
  const outputDirInput = document.getElementById("output_dir").value.trim();
  const btn = document.getElementById("btn");
  const status = document.getElementById("status");

  if (!logo || videos.length === 0) {
    alert("Vui lòng chọn đủ logo và ít nhất 1 video!");
    return;
  }

  const formData = new FormData();
  formData.append("logo", logo);
  // Ưu tiên đường dẫn đã nhập: nếu có text thì server lưu vào đó, không dùng outputs
  const useTypedPath = outputDirInput.length > 0;
  formData.append("use_picker", !useTypedPath && pickedDirHandle ? "1" : "0");
  formData.append("output_dir", outputDirInput);
  for (let i = 0; i < videos.length; i++) {
    formData.append("videos", videos[i]);
  }

  btn.disabled = true;
  status.className = "processing";
  status.innerText = "Đang xử lý... Vui lòng đợi (kiểm tra cửa sổ terminal Python)";

  try {
    const response = await fetch("/process", {
      method: "POST",
      body: formData,
    });
    const result = await response.json();

    if (response.ok) {
      if (pickedDirHandle && result.files && result.files.length > 0) {
        await saveFilesToPickedFolder(result.files);
      } else {
        status.className = "success";
        status.innerText =
          result.message +
          (result.output_folder ? " Thư mục: " + result.output_folder : "");
      }
    } else {
      status.className = "error";
      status.innerText = result.error || "Có lỗi xảy ra!";
    }
  } catch (error) {
    status.className = "error";
    status.innerText = "Có lỗi xảy ra!";
  } finally {
    btn.disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", function () {
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
