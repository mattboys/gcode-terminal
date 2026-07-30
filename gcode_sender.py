import tkinter as tk
from tkinter import ttk, scrolledtext
import serial
import serial.tools.list_ports
import threading
import time

GCODE_COMMANDS = {
    "Movement": [
        ("G28", "Home all axes"),
        ("G28 X", "Home X only"),
        ("G28 Y", "Home Y only"),
        ("G28 Z", "Home Z only"),
        ("G1 X10 F3000", "Move X to 10mm"),
        ("G1 Y10 F3000", "Move Y to 10mm"),
        ("G1 Z10 F3000", "Move Z to 10mm"),
        ("G90", "Absolute positioning"),
        ("G91", "Relative positioning"),
        ("G92 X0 Y0 Z0", "Set current position as zero"),
    ],
    "Extruder": [
        ("M302 P1", "Allow cold extrusion"),
        ("M302 P0", "Disable cold extrusion"),
        ("G91 / G1 E10 F60 / G90", "Extrude 10mm (relative)"),
        ("G91 / G1 E-10 F60 / G90", "Retract 10mm (relative)"),
        ("M82", "Absolute extruder mode"),
        ("M83", "Relative extruder mode"),
    ],
    "Temperature": [
        ("M104 S200", "Set nozzle to 200C (no wait)"),
        ("M104 S0", "Turn off nozzle heater"),
        ("M109 S200", "Set nozzle to 200C (wait)"),
        ("M140 S60", "Set bed to 60C (no wait)"),
        ("M140 S0", "Turn off bed heater"),
        ("M190 S60", "Set bed to 60C (wait)"),
        ("M105", "Report temperatures"),
    ],
    "Cooling": [
        ("M106 S255", "Fan ON (full)"),
        ("M106 S128", "Fan ON (50%)"),
        ("M107", "Fan OFF"),
    ],
    "Status & Info": [
        ("M115", "Firmware info"),
        ("M119", "Endstop status"),
        ("M503", "Current settings (EEPROM)"),
        ("M114", "Current position"),
        ("M420 V", "ABL status / mesh"),
        ("M48", "Probe repeatability test"),
    ],
    "Calibration": [
        ("G29", "Auto bed leveling"),
        ("G29 P1", "Level bed (manual, 4-point)"),
        ("M500", "Save to EEPROM"),
        ("M501", "Load from EEPROM"),
        ("M502", "Factory reset settings"),
        ("M303 E0 S200", "PID autotune nozzle at 200C"),
        ("M304", "PID autotune bed (Marlin)"),
    ],
    "SD Card": [
        ("M21", "Init SD card"),
        ("M20", "List files on SD"),
        ("M23 filename.gco", "Select file"),
        ("M24", "Start/resume print"),
        ("M25", "Pause print"),
        ("M26 S0", "Set SD position to 0"),
        ("M27", "Report SD print status"),
    ],
    "Emergency": [
        ("M112", "Emergency stop"),
        ("M110", "Reset line number"),
        ("M999", "Restart after error"),
    ],
    "Stepper Motors": [
        ("M18", "Disable all steppers"),
        ("M17", "Enable all steppers"),
        ("M84", "Disable steppers after idle"),
    ],
    "Probe / BLTouch": [
        ("M280 P0 S160", "BLTouch alarm release"),
        ("M280 P0 S10", "BLTouch self-test"),
        ("M280 P0 S90", "BLTouch deploy pin"),
        ("M280 P0 S120", "BLTouch stow pin"),
    ],
    "Probe (continued)": [
        ("G30", "Single probe at current pos"),
        ("G30 X100 Y100", "Single probe at X100 Y100"),
        ("M401", "Deploy probe (Marlin)"),
        ("M402", "Stow probe (Marlin)"),
    ],
}


class GCodeSender(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("G-Code Sender")
        self.geometry("950x680")
        self.serial_port = None
        self.reader_active = False
        self.command_history = []
        self._history_index = 0
        self._history_edit_buffer = ""

        self._build_ui()

    def _build_ui(self):
        # ── Serial connection frame ──
        conn = ttk.LabelFrame(self, text="Serial Connection", padding=8)
        conn.pack(fill="x", padx=8, pady=(8, 0))

        ttk.Label(conn, text="Port:").grid(row=0, column=0, padx=(0, 4))
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(conn, textvariable=self.port_var, width=18, state="readonly")
        self.port_combo.grid(row=0, column=1, padx=(0, 12))
        self._refresh_ports()
        ttk.Button(conn, text="↻", width=3, command=self._refresh_ports).grid(row=0, column=2, padx=(0, 12))

        ttk.Label(conn, text="Baud:").grid(row=0, column=3, padx=(0, 4))
        self.baud_var = tk.StringVar(value="115200")
        bauds = ["250000", "115200", "57600", "38400", "19200"]
        self.baud_combo = ttk.Combobox(conn, textvariable=self.baud_var, values=bauds, width=9, state="readonly")
        self.baud_combo.grid(row=0, column=4, padx=(0, 12))

        self.connect_btn = ttk.Button(conn, text="Connect", command=self._toggle_connection)
        self.connect_btn.grid(row=0, column=5)

        self.status_lbl = ttk.Label(conn, text="Disconnected", foreground="gray")
        self.status_lbl.grid(row=0, column=6, padx=(12, 0))

        # ── Side-by-side panels ──
        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=8, pady=8)

        left = ttk.Frame(panes)
        right = ttk.Frame(panes)
        panes.add(left, weight=1)
        panes.add(right, weight=1)

        self._build_terminal_panel(left)
        self._build_commands_panel(right)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Serial helpers ──

    def _refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_combo["values"] = ports
        if ports and not self.port_var.get():
            self.port_var.set(ports[0])

    def _toggle_connection(self):
        if self.serial_port and self.serial_port.is_open:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        port = self.port_var.get()
        if not port:
            self._log("No port selected", "red")
            return
        try:
            baud = int(self.baud_var.get())
            self.serial_port = serial.Serial(port, baud, timeout=0.5, write_timeout=1)
            self.serial_port.dtr = True
            self.serial_port.rts = True
            time.sleep(2)
            self.serial_port.reset_input_buffer()
            self.reader_active = True
            threading.Thread(target=self._reader_loop, daemon=True).start()
            self.connect_btn.config(text="Disconnect")
            self.status_lbl.config(text=f"Connected ({port})", foreground="green")
            self._log(f"Connected to {port} @ {baud} baud", "green")
        except Exception as e:
            self._log(f"Connection failed: {e}", "red")

    def _disconnect(self):
        self.reader_active = False
        if self.serial_port:
            try:
                self.serial_port.close()
            except Exception:
                pass
            self.serial_port = None
        self.connect_btn.config(text="Connect")
        self.status_lbl.config(text="Disconnected", foreground="gray")
        self._log("Disconnected", "orange")

    def _send_gcode(self, cmd):
        cmd = cmd.strip()
        if not cmd:
            return
        self._add_to_history(cmd)
        if not self.serial_port or not self.serial_port.is_open:
            self._log("Not connected", "red")
            return
        try:
            for line in cmd.split(";"):
                line = line.strip()
                if not line:
                    continue
                self.serial_port.write((line + "\n").encode())
            self._log(f">>> {cmd}", "white")
        except Exception as e:
            self._log(f"Send error: {e}", "red")

    def _add_to_history(self, cmd):
        cmd = cmd.strip()
        if not cmd:
            return
        if self.command_history and self.command_history[-1] == cmd:
            return
        self.command_history.append(cmd)
        self._history_index = len(self.command_history)

    def _navigate_history(self, direction):
        if not self.command_history:
            return "break"

        if direction < 0:
            if self._history_index >= len(self.command_history):
                self._history_edit_buffer = self.cmd_var.get()
                self._history_index = len(self.command_history) - 1
            elif self._history_index > 0:
                self._history_index -= 1
            else:
                self._history_index = 0
        else:
            if self._history_index < len(self.command_history):
                if self._history_index == len(self.command_history) - 1:
                    self._history_index = len(self.command_history)
                    self.cmd_var.set(self._history_edit_buffer)
                    return "break"
                self._history_index += 1
            else:
                self._history_index = len(self.command_history)

        if self._history_index < len(self.command_history):
            self.cmd_var.set(self.command_history[self._history_index])

        return "break"

    def _reader_loop(self):
        buf = ""
        while self.reader_active and self.serial_port and self.serial_port.is_open:
            try:
                data = self.serial_port.read(512)
                if data:
                    buf += data.decode("utf-8", errors="replace")
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        line = line.strip()
                        if line:
                            self._log(line, "lime")
            except serial.SerialException:
                break
            except Exception:
                pass

    def _log(self, msg, color="white"):
        self.after(0, lambda: self._do_log(msg, color))

    def _do_log(self, msg, color):
        self.output.config(state="normal")
        self.output.insert("end", msg + "\n")
        self.output.see("end")
        self.output.config(state="disabled")

    def _on_close(self):
        self.reader_active = False
        if self.serial_port and self.serial_port.is_open:
            try:
                self.serial_port.close()
            except Exception:
                pass
        self.destroy()

    # ── Terminal Panel ──

    def _build_terminal_panel(self, parent):
        ttk.Label(parent, text="Terminal", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=6, pady=(6, 0))

        self.output = scrolledtext.ScrolledText(
            parent, state="disabled", font=("Consolas", 10),
            wrap="word", bg="#1e1e1e", fg="#d4d4d4",
            insertbackground="white",
        )
        self.output.pack(fill="both", expand=True, padx=6, pady=6)

        inp = ttk.Frame(parent)
        inp.pack(fill="x", padx=6, pady=(0, 6))

        self.cmd_var = tk.StringVar()
        self.cmd_entry = ttk.Entry(inp, textvariable=self.cmd_var, font=("Consolas", 11))
        self.cmd_entry.pack(side="left", fill="x", expand=True)
        self.cmd_entry.bind("<Return>", lambda e: self._terminal_send())
        self.cmd_entry.bind("<Up>", lambda e: self._navigate_history(-1))
        self.cmd_entry.bind("<Down>", lambda e: self._navigate_history(1))

        ttk.Button(inp, text="Send", command=self._terminal_send).pack(side="left", padx=(6, 0))

        self.output.tag_config("white", foreground="#d4d4d4")
        self.output.tag_config("lime", foreground="#6a9955")
        self.output.tag_config("red", foreground="#f44747")
        self.output.tag_config("green", foreground="#4ec9b0")
        self.output.tag_config("orange", foreground="#ce9178")

    def _terminal_send(self):
        cmd = self.cmd_var.get().strip()
        if not cmd:
            return
        self._send_gcode(cmd)
        self.cmd_var.set("")
        self._history_edit_buffer = ""
        self._history_index = len(self.command_history)

    # ── Commands Panel ──

    def _build_commands_panel(self, parent):
        header_frame = ttk.Frame(parent)
        header_frame.pack(fill="x", padx=6, pady=(6, 0))
        ttk.Label(header_frame, text="Commands", font=("Segoe UI", 10, "bold")).pack(side="left")
        ttk.Button(header_frame, text="Clear Output", command=self._clear_output).pack(side="right")
        ttk.Button(header_frame, text="Send Selected", command=lambda: self._send_selected(tree)).pack(side="right", padx=(0, 6))

        outer = ttk.Frame(parent)
        outer.pack(fill="both", expand=True, padx=6, pady=6)

        # Treeview with category grouping
        tree = ttk.Treeview(outer, columns=("command", "description"), show="tree headings", selectmode="browse")
        tree.heading("#0", text="Category")
        tree.heading("command", text="G-Code")
        tree.heading("description", text="Description")
        tree.column("#0", width=140, minwidth=120)
        tree.column("command", width=200, minwidth=160)
        tree.column("description", width=300, minwidth=200)

        vsb = ttk.Scrollbar(outer, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        for category, commands in GCODE_COMMANDS.items():
            cat_id = tree.insert("", "end", text=category, open=True)
            for cmd, desc in commands:
                tree.insert(cat_id, "end", text="", values=(cmd, desc))

        def on_double_click(event):
            sel = tree.selection()
            if not sel:
                return
            item = sel[0]
            parent = tree.parent(item)
            if not parent:
                return
            vals = tree.item(item, "values")
            if vals:
                self._send_gcode(vals[0])

        tree.bind("<Double-1>", on_double_click)

    def _send_selected(self, tree):
        sel = tree.selection()
        if not sel:
            return
        item = sel[0]
        parent = tree.parent(item)
        if not parent:
            return
        vals = tree.item(item, "values")
        if vals:
            self._send_gcode(vals[0])

    def _clear_output(self):
        self.output.config(state="normal")
        self.output.delete("1.0", "end")
        self.output.config(state="disabled")


if __name__ == "__main__":
    GCodeSender().mainloop()
