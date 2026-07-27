"""Chinese desktop GUI for safe NOX-to-Origin EIS plotting."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import traceback
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from PIL import Image, ImageTk

from . import __version__
from .core import (
    existing_output_folders,
    infer_sample_label,
    run_eis_job,
    validate_area,
)


APP_NAME = "Origin EIS 阻抗图绘制工具"
WINDOW_SIZE = "1120x750"
PREVIEW_SIZE = (440, 330)


def resource_icon() -> Path:
    """Resolve the bundled Windows application icon."""

    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / "app_resources" / "app_icon.ico"
    return Path(__file__).resolve().parents[1] / "packaging" / "app_icon.ico"


def _origin_uninstall_entries() -> list[dict[str, str]]:
    if sys.platform != "win32":
        return []
    import winreg

    entries: list[dict[str, str]] = []
    uninstall_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                uninstall = winreg.OpenKey(
                    root,
                    uninstall_path,
                    0,
                    winreg.KEY_READ | view,
                )
            except OSError:
                continue
            with uninstall:
                for index in range(winreg.QueryInfoKey(uninstall)[0]):
                    try:
                        key_name = winreg.EnumKey(uninstall, index)
                        with winreg.OpenKey(uninstall, key_name) as item:
                            name = str(winreg.QueryValueEx(item, "DisplayName")[0])
                            if not name.lower().startswith("origin"):
                                continue
                            try:
                                version = str(
                                    winreg.QueryValueEx(item, "DisplayVersion")[0]
                                )
                            except OSError:
                                version = ""
                            try:
                                location = str(
                                    winreg.QueryValueEx(item, "InstallLocation")[0]
                                )
                            except OSError:
                                location = ""
                            entries.append(
                                {
                                    "name": name,
                                    "version": version,
                                    "location": location,
                                }
                            )
                    except OSError:
                        continue
    unique = {
        (entry["name"], entry["version"]): entry
        for entry in entries
    }
    return sorted(unique.values(), key=lambda value: value["name"])


def origin_com_registered() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    for prog_id in (
        r"Origin.Application\CLSID",
        r"Origin.ApplicationSI\CLSID",
    ):
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog_id):
                return True
        except OSError:
            continue
    return False


def origin_status() -> dict[str, Any]:
    entries = _origin_uninstall_entries()
    years: list[int] = []
    for entry in entries:
        match = re.search(r"(?<!\d)(20\d{2})(?!\d)", entry["name"])
        if match:
            years.append(int(match.group(1)))
    registered = origin_com_registered()
    compatible = registered and (not years or max(years) >= 2021)
    if entries:
        display = " / ".join(
            f"{entry['name']} {entry['version']}".strip()
            for entry in entries
        )
    elif registered:
        display = "Origin COM 已注册（版本未知）"
    else:
        display = "未检测到 Origin COM"
    return {
        "compatible": compatible,
        "com_registered": registered,
        "entries": entries,
        "years": years,
        "display": display,
    }


def self_test_payload() -> dict[str, Any]:
    import importlib.metadata

    import originpro
    from PIL import __version__ as pillow_version

    status = origin_status()
    icon = resource_icon()
    payload = {
        "app": APP_NAME,
        "version": __version__,
        "frozen": bool(getattr(sys, "frozen", False)),
        "resource_icon": str(icon),
        "resource_icon_exists": icon.is_file(),
        "dependencies": {
            "originpro": importlib.metadata.version("originpro"),
            "OriginExt": importlib.metadata.version("OriginExt"),
            "Pillow": pillow_version,
        },
        "origin": status,
        "originpro_external_api": bool(getattr(originpro, "oext", None)),
        "ok": icon.is_file() and bool(status["compatible"]),
    }
    try:
        if getattr(originpro, "oext", None):
            originpro.exit()
    except Exception:
        pass
    return payload


def _write_worker_result(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def worker_main(request_path: Path, result_path: Path) -> int:
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        result = run_eis_job(
            request["input"],
            float(request["area_cm2"]),
            request["output_directory"],
            request.get("sample_label"),
            overwrite=bool(request.get("overwrite", False)),
        )
        payload = {"ok": True, "result": result}
    except Exception as exc:
        payload = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "details": "".join(
                traceback.format_exception_only(type(exc), exc)
            ).strip(),
        }
    _write_worker_result(result_path, payload)
    return 0 if payload["ok"] else 1


class EISApplication:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.running = False
        self.last_result: dict[str, Any] | None = None
        self.preview_photos: dict[str, ImageTk.PhotoImage] = {}

        root.title(f"{APP_NAME}  v{__version__}")
        try:
            root.iconbitmap(default=str(resource_icon()))
        except tk.TclError:
            pass
        root.geometry(WINDOW_SIZE)
        root.minsize(980, 680)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.input_var = tk.StringVar()
        self.area_var = tk.StringVar()
        self.label_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.status_var = tk.StringVar(value="请选择 NOX 文件或文件夹。")
        self.origin_var = tk.StringVar()

        self._configure_style()
        self._build_ui()
        self._refresh_origin_status()

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        for theme in ("vista", "xpnative", "clam"):
            if theme in style.theme_names():
                style.theme_use(theme)
                break
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("Subtitle.TLabel", font=("Microsoft YaHei UI", 10))
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 11, "bold"))

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=18)
        outer.grid(row=0, column=0, sticky="nsew")
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        outer.columnconfigure(0, weight=5)
        outer.columnconfigure(1, weight=4)
        outer.rowconfigure(2, weight=1)

        ttk.Label(
            outer,
            text="Origin EIS 阻抗图绘制工具",
            style="Title.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(
            outer,
            text="安全解析 NOVA NOX，按电极面积归一化，并生成原始与截距平移 Nyquist 图",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 14))

        controls = ttk.LabelFrame(outer, text="绘图任务", padding=14)
        controls.grid(row=2, column=0, sticky="nsew", padx=(0, 10))
        controls.columnconfigure(1, weight=1)
        controls.rowconfigure(9, weight=1)

        ttk.Label(controls, text="NOX 输入").grid(
            row=0,
            column=0,
            sticky="w",
            pady=5,
        )
        self.input_entry = ttk.Entry(controls, textvariable=self.input_var)
        self.input_entry.grid(row=0, column=1, sticky="ew", padx=8, pady=5)
        input_buttons = ttk.Frame(controls)
        input_buttons.grid(row=0, column=2, pady=5)
        self.file_button = ttk.Button(
            input_buttons,
            text="文件…",
            width=8,
            command=self.choose_file,
        )
        self.file_button.grid(row=0, column=0)
        self.folder_button = ttk.Button(
            input_buttons,
            text="文件夹…",
            width=9,
            command=self.choose_input_folder,
        )
        self.folder_button.grid(row=0, column=1, padx=(4, 0))

        ttk.Label(controls, text="有效面积").grid(
            row=1,
            column=0,
            sticky="w",
            pady=5,
        )
        area_frame = ttk.Frame(controls)
        area_frame.grid(row=1, column=1, columnspan=2, sticky="ew", padx=8, pady=5)
        self.area_entry = ttk.Entry(
            area_frame,
            textvariable=self.area_var,
            width=18,
        )
        self.area_entry.grid(row=0, column=0, sticky="w")
        ttk.Label(area_frame, text="cm²").grid(
            row=0,
            column=1,
            sticky="w",
            padx=(8, 0),
        )

        ttk.Label(controls, text="样品标签").grid(
            row=2,
            column=0,
            sticky="w",
            pady=5,
        )
        self.label_entry = ttk.Entry(controls, textvariable=self.label_var)
        self.label_entry.grid(
            row=2,
            column=1,
            columnspan=2,
            sticky="ew",
            padx=8,
            pady=5,
        )

        ttk.Label(controls, text="输出目录").grid(
            row=3,
            column=0,
            sticky="w",
            pady=5,
        )
        self.output_entry = ttk.Entry(controls, textvariable=self.output_var)
        self.output_entry.grid(row=3, column=1, sticky="ew", padx=8, pady=5)
        self.output_button = ttk.Button(
            controls,
            text="选择目录…",
            command=self.choose_output,
        )
        self.output_button.grid(row=3, column=2, pady=5)

        ttk.Separator(controls).grid(
            row=4,
            column=0,
            columnspan=3,
            sticky="ew",
            pady=10,
        )
        ttk.Label(controls, text="Origin 环境").grid(
            row=5,
            column=0,
            sticky="nw",
        )
        ttk.Label(
            controls,
            textvariable=self.origin_var,
            wraplength=500,
        ).grid(row=5, column=1, columnspan=2, sticky="w", padx=8)

        actions = ttk.Frame(controls)
        actions.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(14, 8))
        actions.columnconfigure(0, weight=1)
        self.start_button = ttk.Button(
            actions,
            text="开始处理并绘图",
            style="Accent.TButton",
            command=self.start,
        )
        self.start_button.grid(row=0, column=0, sticky="ew")
        self.open_button = ttk.Button(
            actions,
            text="打开输出目录",
            command=self.open_output,
            state="disabled",
        )
        self.open_button.grid(row=0, column=1, padx=(8, 0))

        self.progress = ttk.Progressbar(controls, mode="indeterminate")
        self.progress.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(4, 6))
        ttk.Label(
            controls,
            textvariable=self.status_var,
            wraplength=570,
        ).grid(row=8, column=0, columnspan=3, sticky="w", pady=(2, 8))

        self.log = scrolledtext.ScrolledText(
            controls,
            height=10,
            wrap=tk.WORD,
            state="disabled",
            font=("Consolas", 9),
        )
        self.log.grid(row=9, column=0, columnspan=3, sticky="nsew")

        preview_box = ttk.LabelFrame(outer, text="PNG 预览", padding=10)
        preview_box.grid(row=2, column=1, sticky="nsew", padx=(10, 0))
        preview_box.rowconfigure(0, weight=1)
        preview_box.columnconfigure(0, weight=1)
        self.preview_tabs = ttk.Notebook(preview_box)
        self.preview_tabs.grid(row=0, column=0, sticky="nsew")
        self.original_preview = ttk.Label(
            self.preview_tabs,
            text="完成后显示原始面积归一化图",
            anchor="center",
            justify="center",
        )
        self.final_preview = ttk.Label(
            self.preview_tabs,
            text="完成后显示 Y=0 截距平移最终图",
            anchor="center",
            justify="center",
        )
        self.preview_tabs.add(self.original_preview, text="原始 EIS 图")
        self.preview_tabs.add(self.final_preview, text="最终 EIS 图")

        ttk.Label(
            outer,
            text=(
                "输出固定为：NOX处理结果 / 原始EIS图 / 最终EIS图；"
                "源 NOX 始终按只读方式处理。"
            ),
            foreground="#666666",
        ).grid(row=3, column=0, sticky="w", pady=(12, 0))
        ttk.Label(
            outer,
            text="Designed by WangXi",
            foreground="#777777",
            font=("Segoe UI", 9, "italic"),
        ).grid(row=3, column=1, sticky="e", pady=(12, 0))

    def _refresh_origin_status(self) -> None:
        status = origin_status()
        if status["compatible"]:
            self.origin_var.set(f"✓ {status['display']}（COM 可用）")
        elif status["com_registered"]:
            self.origin_var.set(f"⚠ {status['display']}（需要 Origin 2021+）")
        else:
            self.origin_var.set("✗ 未检测到 Origin COM；请安装并启动一次 Origin。")

    def _after_input_selected(self, selected: str) -> None:
        path = Path(selected)
        self.input_var.set(str(path))
        try:
            self.label_var.set(infer_sample_label(path))
        except Exception:
            pass
        if not self.output_var.get().strip():
            base = path.parent if path.is_file() else path
            self.output_var.set(str(base / "EIS绘图输出"))
        self.status_var.set("输入已选择，请填写有效电极面积。")

    def choose_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="选择 NOVA NOX 文件",
            filetypes=[("NOVA NOX", "*.nox"), ("所有文件", "*.*")],
        )
        if selected:
            self._after_input_selected(selected)

    def choose_input_folder(self) -> None:
        selected = filedialog.askdirectory(title="选择包含 NOX 的文件夹")
        if selected:
            self._after_input_selected(selected)

    def choose_output(self) -> None:
        initial = self.output_var.get().strip()
        options: dict[str, Any] = {"title": "选择输出目录"}
        if initial and Path(initial).exists():
            options["initialdir"] = initial
        selected = filedialog.askdirectory(**options)
        if selected:
            self.output_var.set(selected)

    def _set_running(self, running: bool) -> None:
        self.running = running
        state = "disabled" if running else "normal"
        for widget in (
            self.input_entry,
            self.area_entry,
            self.label_entry,
            self.output_entry,
            self.file_button,
            self.folder_button,
            self.output_button,
            self.start_button,
        ):
            widget.configure(state=state)
        if running:
            self.progress.start(10)
            self.open_button.configure(state="disabled")
        else:
            self.progress.stop()

    def _write_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert(tk.END, text.rstrip() + "\n")
        self.log.see(tk.END)
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", tk.END)
        self.log.configure(state="disabled")

    def start(self) -> None:
        input_text = self.input_var.get().strip().strip('"')
        output_text = self.output_var.get().strip().strip('"')
        if not input_text:
            messagebox.showwarning("缺少输入", "请选择 NOX 文件或文件夹。")
            return
        try:
            area = validate_area(float(self.area_var.get().strip()))
        except (ValueError, TypeError):
            messagebox.showerror(
                "面积无效",
                "请输入大于 0 的有效电极面积，单位为 cm²。",
            )
            return
        if not output_text:
            messagebox.showwarning("缺少输出", "请选择输出目录。")
            return

        input_path = Path(input_text)
        output_path = Path(output_text)
        try:
            inferred = infer_sample_label(input_path)
        except Exception as exc:
            messagebox.showerror("输入无效", str(exc))
            return
        label = self.label_var.get().strip() or inferred
        existing = existing_output_folders(output_path)
        overwrite = bool(existing)
        if overwrite:
            names = "、".join(path.name for path in existing)
            if not messagebox.askyesno(
                "确认替换结果",
                "输出目录中已存在以下固定结果文件夹：\n"
                f"{names}\n\n"
                "本次任务成功并通过校验后，将替换这些结果文件夹。"
                "输出目录中的其他文件不会被删除。\n\n是否继续？",
            ):
                return

        self._clear_log()
        self.preview_photos.clear()
        self.original_preview.configure(image="", text="正在生成原始 EIS 图…")
        self.final_preview.configure(image="", text="正在生成最终 EIS 图…")
        self.last_result = None
        self._set_running(True)
        self.status_var.set("正在解析 NOX 并调用 Origin，请稍候…")
        self._write_log(
            f"输入：{input_path}\n面积：{area:.12g} cm²\n"
            f"样品标签：{label}\n输出：{output_path}"
        )
        request = {
            "input": str(input_path),
            "area_cm2": area,
            "sample_label": label,
            "output_directory": str(output_path),
            "overwrite": overwrite,
        }
        threading.Thread(
            target=self._run_request,
            args=(request,),
            daemon=True,
        ).start()

    def _run_request(self, request: dict[str, Any]) -> None:
        try:
            if getattr(sys, "frozen", False):
                with tempfile.TemporaryDirectory(prefix="eis_gui_") as temporary:
                    request_path = Path(temporary) / "request.json"
                    result_path = Path(temporary) / "result.json"
                    request_path.write_text(
                        json.dumps(request, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    completed = subprocess.run(
                        [
                            sys.executable,
                            "--worker",
                            str(request_path),
                            str(result_path),
                        ],
                        capture_output=True,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                        check=False,
                    )
                    if not result_path.is_file():
                        raise RuntimeError(
                            f"后台绘图进程异常退出（代码 {completed.returncode}）。"
                        )
                    payload = json.loads(result_path.read_text(encoding="utf-8"))
            else:
                result = run_eis_job(
                    request["input"],
                    request["area_cm2"],
                    request["output_directory"],
                    request["sample_label"],
                    overwrite=request["overwrite"],
                )
                payload = {"ok": True, "result": result}
            if not payload.get("ok"):
                raise RuntimeError(payload.get("details") or payload.get("error"))
        except Exception as exc:
            details = "".join(
                traceback.format_exception_only(type(exc), exc)
            ).strip()
            self.root.after(0, self._finish_error, details)
            return
        self.root.after(0, self._finish_success, payload["result"])

    def _finish_success(self, result: dict[str, Any]) -> None:
        self._set_running(False)
        self.last_result = result
        self.open_button.configure(state="normal")
        summary = (
            "处理完成并通过自动校验。\n"
            f"NOX 文件：{result['nox_files']}\n"
            f"EIS 数据集：{result['datasets']}\n"
            f"数据点：{result['points']}\n"
            f"有效面积：{float(result['area_cm2']):.6g} cm²\n"
            f"原始 OPJU：{result['original_opju']}\n"
            f"最终 OPJU：{result['final_opju']}"
        )
        self.status_var.set("处理完成。")
        self._write_log(summary)
        self._show_preview(
            self.original_preview,
            Path(result["original_png"]),
            "original",
        )
        self._show_preview(
            self.final_preview,
            Path(result["final_png"]),
            "final",
        )
        messagebox.showinfo(
            "处理完成",
            "已生成面积归一化 CSV、原始 EIS 图和最终 EIS 图。\n\n"
            f"{result['nox_files']} 个 NOX，"
            f"{result['datasets']} 个数据集，"
            f"{result['points']} 个数据点。",
        )

    def _finish_error(self, details: str) -> None:
        self._set_running(False)
        self.status_var.set("处理失败，请查看错误信息。")
        self._write_log("错误：\n" + details)
        self.original_preview.configure(image="", text="未生成预览")
        self.final_preview.configure(image="", text="未生成预览")
        messagebox.showerror("处理失败", details)

    def _show_preview(
        self,
        target: ttk.Label,
        path: Path,
        key: str,
    ) -> None:
        try:
            with Image.open(path) as image:
                preview = image.convert("RGB")
                preview.thumbnail(PREVIEW_SIZE, Image.Resampling.LANCZOS)
                preview = preview.copy()
            photo = ImageTk.PhotoImage(preview)
            self.preview_photos[key] = photo
            target.configure(image=photo, text="")
        except Exception as exc:
            target.configure(image="", text=f"PNG 已生成，但预览失败：\n{exc}")

    def open_output(self) -> None:
        path = Path(self.output_var.get().strip().strip('"'))
        if not path.exists():
            messagebox.showwarning("目录不存在", f"找不到输出目录：\n{path}")
            return
        os.startfile(path)  # type: ignore[attr-defined]

    def on_close(self) -> None:
        if self.running:
            messagebox.showinfo(
                "任务正在运行",
                "Origin 自动化尚未结束，请等待任务完成后再关闭窗口。",
            )
            return
        self.root.destroy()


def configure_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "--worker":
        if len(arguments) != 3:
            return 2
        return worker_main(
            Path(arguments[1]).resolve(),
            Path(arguments[2]).resolve(),
        )
    if arguments and arguments[0] == "--self-test":
        if len(arguments) != 2:
            return 2
        output = Path(arguments[1]).resolve()
        try:
            payload = self_test_payload()
        except Exception as exc:
            payload = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        _write_worker_result(output, payload)
        return 0 if payload.get("ok") else 1

    configure_dpi_awareness()
    root = tk.Tk()
    EISApplication(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
