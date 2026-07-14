const result = document.querySelector("#result");

document.querySelector("#upload").addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = document.querySelector("#file").files[0];
  if (!file) {
    result.textContent = "请选择 DOCX 文件";
    return;
  }
  const body = new FormData();
  body.append("file", file);
  result.textContent = "正在上传并建立本地案例…";
  const upload = await fetch("/api/cases", { method: "POST", body });
  const uploaded = await upload.json();
  if (!upload.ok) {
    result.textContent = uploaded.detail || "上传失败";
    return;
  }
  result.textContent = "正在执行初审…";
  const review = await fetch(`/api/cases/${uploaded.case_id}/review`, { method: "POST" });
  const reviewed = await review.json();
  result.textContent = review.ok
    ? `案例 ${uploaded.case_id}\n${JSON.stringify(reviewed, null, 2)}`
    : (reviewed.detail || "初审失败");
});
