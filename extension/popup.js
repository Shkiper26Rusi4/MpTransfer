const status = document.getElementById("status");
async function refresh(action) {
  const response = await chrome.runtime.sendMessage({action});
  status.textContent = response.status;
}
document.getElementById("connect").onclick = () => refresh("connect");
refresh("status");
setInterval(() => refresh("status"),1000);
