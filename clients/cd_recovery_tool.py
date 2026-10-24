import customtkinter as ctk
import json
import subprocess
import sys
import threading
import os

# Run as `python clients/cd_recovery_tool.py`: make the project root importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tkinter import filedialog, messagebox

from vs_app.core.recovery import (
    IMAGE_BUSY_EXIT,
    build_ddrescue_argv,
    build_photorec_argv,
    image_busy_error,
    is_optical_device,
    locked_argv,
    mapfile_extent,
    parse_lsblk_drives,
    parse_lsblk_pairs,
    source_mismatch,
    source_mismatch_error,
)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

DRIVE_LIST_CMD = ["lsblk", "-P", "-o", "NAME,TYPE,SIZE,TRAN,RM,MODEL,LOG-SEC"]


def device_identity(device):
    """Size and serial/UUID/label of a device, the same record the web backend keeps."""
    try:
        out = subprocess.check_output(
            ["lsblk", "-b", "-d", "-n", "-P", "-o", "NAME,SIZE,SERIAL,UUID,LABEL", device],
            text=True, stderr=subprocess.DEVNULL, timeout=20)
    except Exception:
        return {}
    props = (parse_lsblk_pairs(out) or [{}])[0]
    size = props.get("SIZE", "")
    return {"size": int(size) if size.isdigit() else None, "serial": props.get("SERIAL", "").strip(),
            "uuid": props.get("UUID", "").strip(), "label": props.get("LABEL", "").strip()}


def check_image_source(image, device):
    """Refuse to resume an image made from another disc; record the source otherwise."""
    record_path = image + ".source.json"
    identity = device_identity(device)
    saved = {}
    if os.path.exists(record_path):
        try:
            with open(record_path, encoding="utf-8") as handle:
                saved = json.load(handle)
        except (OSError, ValueError):
            saved = {}
    if os.path.exists(image) or os.path.exists(image + ".log"):
        extent = None
        if os.path.exists(image + ".log"):
            with open(image + ".log", encoding="utf-8", errors="replace") as handle:
                extent = mapfile_extent(handle.read())
        reason = source_mismatch(saved, identity, extent)
        if reason:
            raise RuntimeError(source_mismatch_error(image, reason))
    if not saved:
        with open(record_path, "w", encoding="utf-8") as handle:
            json.dump(dict(identity, device=device), handle, indent=2)


class DriveRecoveryTool(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Ultimate CD/DVD + Pen Drive Recovery Tool - WSL + RTX 3050")
        self.geometry("1080x820")

        self.device_map = {}
        self.last_output_dir = None

        ctk.CTkLabel(self, text="CD/DVD + Pen Drive Recovery & High Quality Video Ripper",
                     font=ctk.CTkFont(size=22, weight="bold")).pack(pady=15)

        ctk.CTkLabel(self, text="Detected Drives (optical + USB flash):").pack(anchor="w", padx=20)
        self.drive_var = ctk.StringVar()
        self.drive_combo = ctk.CTkComboBox(self, values=[], variable=self.drive_var, width=680)
        self.drive_combo.pack(pady=8, padx=20)
        ctk.CTkButton(self, text="🔄 Refresh Drives", command=self.refresh_drives).pack(pady=5)

        ctk.CTkLabel(self, text="Output Folder:").pack(anchor="w", padx=20, pady=(10, 0))
        self.output_dir = ctk.CTkEntry(self, width=820, placeholder_text="Select folder to save image + carved files...")
        self.output_dir.pack(pady=5, padx=20)
        ctk.CTkButton(self, text="Browse", command=self.browse_folder).pack(pady=5)

        self.mode_var = ctk.StringVar(value="normal")
        ctk.CTkRadioButton(
            self, text="Normal Recovery (pen drive: -b <sector> -r 3 -v / optical: -b 2048 -n -v)",
            variable=self.mode_var, value="normal").pack(anchor="w", padx=30)
        ctk.CTkRadioButton(
            self, text="Aggressive Recovery (direct I/O + 5 retries — scratched discs / degraded flash)",
            variable=self.mode_var, value="aggressive").pack(anchor="w", padx=30)

        ctk.CTkLabel(self, text="File Carving (formatted drive recovery)", 
                    font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=20, pady=(15, 5))

        self.carve_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(self, text="Carve ALL files with PhotoRec after imaging (paranoid + whole space)",
                        variable=self.carve_var).pack(anchor="w", padx=30)

        self.start_btn = ctk.CTkButton(self, text="🚀 Start Recovery (Image + Carve)", 
                                      height=60, font=ctk.CTkFont(size=17, weight="bold"),
                                      command=self.start_process)
        self.start_btn.pack(pady=15)

        self.open_btn = ctk.CTkButton(self, text="📂 Open Recovery Folder", state="disabled",
                                      command=self.open_output_folder)
        self.open_btn.pack(pady=5)

        ctk.CTkLabel(self, text="Live Log:").pack(anchor="w", padx=20)
        self.log_text = ctk.CTkTextbox(self, height=250, width=1030)
        self.log_text.pack(pady=10, padx=20)

        self.refresh_drives()

    def log(self, msg):
        def _append():
            self.log_text.insert("end", f"{msg}\n")
            self.log_text.see("end")
        self.after(0, _append)

    def run_stream(self, cmd):
        self.log("$ " + " ".join(cmd))
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors="replace", bufsize=1)
        for line in proc.stdout:
            line = line.strip("\r\n")
            if line:
                self.log(line)
        proc.wait()
        return proc.returncode

    def refresh_drives(self):
        try:
            result = subprocess.check_output(DRIVE_LIST_CMD, text=True, stderr=subprocess.DEVNULL)
        except Exception:
            self.log("❌ lsblk not found — run this tool inside WSL2 Ubuntu or install util-linux.")
            return

        self.device_map = {}
        entries = []
        for drive in parse_lsblk_drives(result):
            label = (
                f"{'💿 Optical' if drive['type'] == 'optical' else '💾 Pen Drive/USB'}"
                f" · {drive['device']} · {drive['size']} · {drive['model']}"
            )
            self.device_map[label] = (drive['device'], drive['sector_size'])
            entries.append(label)

        values = entries if entries else ["No drive detected — share it with WSL first"]
        self.drive_combo.configure(values=values)
        self.drive_combo.set(values[0])
        if entries:
            self.log(f"✅ Detected {len(entries)} drive(s).")
        else:
            self.log("⚠️ No optical/USB drive detected. Plug the drive in and click Refresh; "
                     "a new USB port needs a one-time share (web app: Detect Drives → Share with WSL, "
                     "or as admin: usbipd list → usbipd bind --busid <BUSID>).")

    def browse_folder(self):
        path = filedialog.askdirectory()
        if path:
            self.output_dir.delete(0, "end")
            self.output_dir.insert(0, path)

    def start_process(self):
        selection = self.drive_var.get()
        out_dir = self.output_dir.get().strip()

        if selection not in self.device_map:
            messagebox.showerror("Error", "Please select a detected drive (click Refresh Drives).")
            return
        if not out_dir:
            messagebox.showerror("Error", "Please select an output folder.")
            return

        device, sector = self.device_map[selection]
        self.start_btn.configure(state="disabled")
        self.open_btn.configure(state="disabled")
        threading.Thread(target=self.run_full_process, args=(device, sector, out_dir), daemon=True).start()

    def run_full_process(self, device, sector, out_dir):
        optical = is_optical_device(device)
        image = os.path.join(out_dir, "cd_backup.iso" if optical else "pendrive_backup.img")
        mode = self.mode_var.get()

        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as e:
            self.log(f"❌ Cannot create output folder: {e}")
            self._finish()
            return

        self.log(f"🚀 Starting recovery from {device} ({'optical 2048B' if optical else f'pen drive {sector}B'} sectors)...")
        dd_cmd = locked_argv(build_ddrescue_argv(device, image, mode, sector), image)
        try:
            check_image_source(image, device)
            rc = self.run_stream(dd_cmd)
            if rc == IMAGE_BUSY_EXIT:
                raise RuntimeError(image_busy_error(image))
            if rc != 0 and not os.path.exists(image):
                raise RuntimeError(f"ddrescue exited with code {rc} and no image was produced.")
            if rc != 0:
                self.log(f"⚠️ ddrescue exited with {rc} (unrescued sectors) — continuing with the partial image. Re-run to retry failed areas.")
            self.log(f"✅ Raw image created: {image}")

            if self.carve_var.get():
                recup_dir = os.path.join(out_dir, "carved")
                os.makedirs(recup_dir, exist_ok=True)
                pc_cmd = locked_argv(build_photorec_argv(image, recup_dir), image)
                self.log("🧩 Carving all files with PhotoRec (paranoid + whole space + keep corrupted files)...")
                rc = self.run_stream(pc_cmd)
                if rc == IMAGE_BUSY_EXIT:
                    raise RuntimeError(image_busy_error(image))
                if rc != 0:
                    self.log(f"⚠️ photorec exited with code {rc} — counting whatever was carved anyway.")
                count = self.count_recovered_files(recup_dir)
                self.log(f"✅ Carving complete — {count} file(s) recovered into {recup_dir}.1, .2, ... folders.")
                self.last_output_dir = recup_dir
            else:
                self.last_output_dir = out_dir

            self.after(0, lambda: self.open_btn.configure(state="normal"))
            self.after(0, lambda: messagebox.showinfo(
                "Success", f"Recovery completed!\nImage: {image}\nRecovered files: {self.last_output_dir}"))
        except FileNotFoundError as e:
            self.log(f"❌ Required tool missing: {e}. Install inside WSL2: sudo apt install -y gddrescue testdisk")
        except Exception as e:
            self.log(f"❌ Error: {e}")
        finally:
            self._finish()

    def _finish(self):
        self.after(0, lambda: self.start_btn.configure(state="normal"))

    def count_recovered_files(self, recup_dir):
        parent = os.path.dirname(recup_dir.rstrip("/"))
        base = os.path.basename(recup_dir.rstrip("/"))
        skip = {"photorec.log", "report.xml"}
        total = 0
        try:
            for name in os.listdir(parent):
                if name == base or name.startswith(base + "."):
                    for _, _, files in os.walk(os.path.join(parent, name)):
                        total += len([f for f in files if f not in skip])
        except Exception:
            return 0
        return total

    def open_output_folder(self):
        if not self.last_output_dir:
            return
        try:
            win_path = subprocess.check_output(["wslpath", "-w", self.last_output_dir], text=True).strip()
            parent = os.path.dirname(win_path) or win_path
            subprocess.Popen(["explorer.exe", parent])
        except Exception as e:
            self.log(f"⚠️ Could not open folder: {e}")


if __name__ == "__main__":
    app = DriveRecoveryTool()
    app.mainloop()
