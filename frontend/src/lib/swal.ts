import Swal from "sweetalert2";

export const modalSwal = Swal.mixin({
  background: "var(--panel, #17191c)",
  color: "var(--text, #f2f3f5)",
  confirmButtonColor: "#86d427",
  cancelButtonColor: "#2b2e33",
  customClass: {
    popup: "mm-swal-popup",
    title: "mm-swal-title",
    htmlContainer: "mm-swal-html",
    confirmButton: "mm-swal-confirm",
    cancelButton: "mm-swal-cancel",
    input: "mm-swal-input",
  },
});
