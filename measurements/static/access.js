(() => {
  const dialog = document.getElementById("measurementAccessDialog");
  if (!dialog) return;
  const open = () => {
    if (!dialog.open) dialog.showModal();
    document.getElementById("measurementAccessPassword").focus();
  };
  document.getElementById("measurementAccessTrigger").addEventListener("dblclick", open);
  document.addEventListener("keydown", (event) => {
    if (event.ctrlKey && event.shiftKey && event.key.toLowerCase() === "m") {
      event.preventDefault();
      open();
    }
  });
  document.getElementById("measurementAccessCancel").addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => {
    document.getElementById("measurementAccessPassword").value = "";
  });
  if (dialog.dataset.reopen === "true") open();
})();
