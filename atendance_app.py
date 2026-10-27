import os
os.environ["DYLD_LIBRARY_PATH"] = "/opt/homebrew/lib"
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import cv2
from PIL import Image, ImageTk
from pyzbar.pyzbar import decode
import mysql.connector
from mysql.connector import Error
import subprocess
import csv
import json
import math
import urllib.request
from datetime import datetime, timedelta

# ==========================================
# LOCAL DATABASE CONFIGURATION
# ==========================================
DB_CONFIG = {
    "host": "localhost",
    "user": "root",          # Replace with your local MySQL username
    "password": "password",  # Replace with your local MySQL password
    "database": "smart_attendance",
    "port": 3306
}

# ==========================================
# GEOFENCE / LOCATION CONFIGURATION
# ==========================================
# Set to None to auto-calibrate to your Mac's current location on startup,
# or hardcode your campus coordinates e.g. (21.2514, 81.6296)
CAMPUS_LAT = None
CAMPUS_LON = None
ALLOWED_RADIUS_METERS = 500  # Maximum allowed distance from classroom in metres

COLOR_VALID = "#ccffcc"      # Pastel Green
COLOR_DUPLICATE = "#ffcccc"  # Pastel Red
COLOR_UNKNOWN = "#ffe4b5"    # Pastel Orange

AUTO_RESET_SECONDS = 12      # Auto-clear screen if a student walks away


def format_mysql_time(val):
    """Safely format MySQL TIME (returned as timedelta) into 12-hour AM/PM string in Python."""
    if val is None:
        return ""
    if isinstance(val, timedelta):
        total_seconds = int(val.total_seconds())
        hours = (total_seconds // 3600) % 24
        minutes = (total_seconds % 3600) // 60
        dt = datetime(2000, 1, 1, hours, minutes)
        return dt.strftime("%I:%M %p")
    if isinstance(val, datetime):
        return val.strftime("%I:%M %p")
    return str(val)


def haversine_meters(lat1, lon1, lat2, lon2):
    """Calculate the great-circle distance in metres between two GPS coordinates."""
    r = 6371000.0  # Earth radius in metres
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (math.sin(delta_phi / 2.0) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return r * c


class StudentAttendanceKiosk:
    def __init__(self, root):
        self.root = root
        self.root.title("Student Self-Service Attendance Kiosk")
        self.root.geometry("1020x830")
        self.root.resizable(False, False)

        # Capture the default system background color for resetting later
        self.default_bg = self.root.cget("bg")

        # Application State
        self.scanning = True
        self.current_student = None
        self.current_period = None
        self.existing_attendance_id = None
        self.action_mode = None             # "CONFIRM" or "REMOVE"
        self.db_conn = None
        self.auto_reset_job = None
        self.countdown_remaining = 0

        # Location State
        self.campus_lat = CAMPUS_LAT
        self.campus_lon = CAMPUS_LON
        self.current_lat = None
        self.current_lon = None
        self.current_city = "Unknown"
        self.simulate_out_of_bounds = tk.BooleanVar(value=False)

        # Initialize Database, Schema & Camera
        self.connect_db()
        self.ensure_location_column()
        self.cap = cv2.VideoCapture(0)

        # Build UI
        self.setup_ui()

        # Calibrate / Scan initial location
        self.calibrate_campus_location()

        # Bind Student-Friendly Keyboard Shortcuts
        self.root.bind("<Return>", lambda event: self.handle_primary_action())
        self.root.bind("<space>", lambda event: self.handle_primary_action())
        self.root.bind("<Escape>", lambda event: self.reset_scanner())
        self.root.bind("h", lambda event: self.show_student_history_popup())
        self.root.bind("H", lambda event: self.show_student_history_popup())
        self.root.bind("t", lambda event: self.show_timetable_popup())
        self.root.bind("T", lambda event: self.show_timetable_popup())
        self.root.bind("l", lambda event: self.toggle_location_simulation())
        self.root.bind("L", lambda event: self.toggle_location_simulation())

        # Ensure clean exit on window close
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Start loops
        self.update_clock()
        self.update_frame()

    def connect_db(self):
        """Establish connection to local MySQL 8.4 database."""
        try:
            self.db_conn = mysql.connector.connect(**DB_CONFIG)
        except Error as e:
            messagebox.showerror(
                "Database Connection Error",
                f"Failed to connect to MySQL:\n{e}\n\nPlease check DB_CONFIG."
            )

    def ensure_db_connection(self):
        """Reconnect if the MySQL connection timed out."""
        if self.db_conn is None or not self.db_conn.is_connected():
            self.connect_db()
        return self.db_conn is not None and self.db_conn.is_connected()

    def ensure_location_column(self):
        """Ensure the attendance table has a location_coords column."""
        if not self.ensure_db_connection():
            return
        try:
            cursor = self.db_conn.cursor()
            cursor.execute("SHOW COLUMNS FROM attendance LIKE 'location_coords'")
            if not cursor.fetchone():
                cursor.execute(
                    "ALTER TABLE attendance ADD COLUMN location_coords VARCHAR(100) DEFAULT 'Verified Campus'"
                )
                self.db_conn.commit()
            cursor.close()
        except Error:
            pass

    def fetch_live_gps(self):
        """
        Scan current device coordinates via IP Geolocation.
        If 'Simulate Out-of-Range [L]' is checked, returns coordinates ~5 km away for testing.
        """
        try:
            req = urllib.request.Request(
                "https://ipinfo.io/json",
                headers={"User-Agent": "SmartAttendanceKiosk/1.0"}
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                loc_parts = data.get("loc", "21.2514,81.6296").split(",")
                lat, lon = float(loc_parts[0]), float(loc_parts[1])
                city = data.get("city", "Campus Network")
        except Exception:
            # Fallback to campus coordinates if offline
            lat = self.campus_lat if self.campus_lat is not None else 21.2514
            lon = self.campus_lon if self.campus_lon is not None else 81.6296
            city = "Local Campus Network"

        if self.simulate_out_of_bounds.get():
            # Offset latitude by ~0.045 degrees (~5,000 metres away) to test failure state
            return lat + 0.045, lon + 0.045, f"Off-Campus ({city})"

        return lat, lon, city

    def calibrate_campus_location(self):
        """Set the classroom reference coordinates on startup and update the GPS banner."""
        lat, lon, city = self.fetch_live_gps()
        if self.campus_lat is None or self.campus_lon is None:
            self.campus_lat = lat
            self.campus_lon = lon
        self.current_lat = lat
        self.current_lon = lon
        self.current_city = city
        self.update_location_banner()

    def toggle_location_simulation(self):
        """Toggle the out-of-range location simulator via the [L] shortcut."""
        self.simulate_out_of_bounds.set(not self.simulate_out_of_bounds.get())
        self.update_location_banner()

    def update_location_banner(self):
        """Refresh the top location status bar."""
        lat, lon, city = self.fetch_live_gps()
        self.current_lat, self.current_lon, self.current_city = lat, lon, city
        dist = haversine_meters(self.campus_lat, self.campus_lon, lat, lon)

        if dist <= ALLOWED_RADIUS_METERS:
            self.location_label.config(
                text=f"📍 Location Verified: {city} ({lat:.4f}, {lon:.4f}) — Within Classroom Zone ({int(dist)}m / {ALLOWED_RADIUS_METERS}m)",
                fg="#065f46"
            )
        else:
            self.location_label.config(
                text=f"⚠️ Outside Classroom Zone: ({lat:.4f}, {lon:.4f}) is {int(dist)}m away (Max allowed: {ALLOWED_RADIUS_METERS}m)",
                fg="#991b1b"
            )

    def verify_student_location(self):
        """Scan location right before marking attendance and return (is_valid, distance_m, coords_str)."""
        lat, lon, city = self.fetch_live_gps()
        self.current_lat, self.current_lon, self.current_city = lat, lon, city
        dist = haversine_meters(self.campus_lat, self.campus_lon, lat, lon)
        self.update_location_banner()
        coords_str = f"{lat:.4f}, {lon:.4f} ({city})"
        return (dist <= ALLOWED_RADIUS_METERS), int(dist), coords_str

    def setup_ui(self):
        """Build the Student Kiosk GUI."""
        self.main_frame = tk.Frame(self.root, bg=self.default_bg, padx=20, pady=10)
        self.main_frame.pack(fill=tk.BOTH, expand=True)

        # Top Bar: Kiosk Header, Test Location Toggle & Live Clock
        self.header_frame = tk.Frame(self.main_frame, bg=self.default_bg)
        self.header_frame.pack(fill=tk.X, pady=(0, 4))

        self.title_label = tk.Label(
            self.header_frame,
            text="Student Attendance Kiosk",
            font=("Helvetica", 22, "bold"),
            bg=self.default_bg
        )
        self.title_label.pack(side=tk.LEFT)

        self.clock_label = tk.Label(
            self.header_frame,
            text="",
            font=("Helvetica", 13),
            bg=self.default_bg
        )
        self.clock_label.pack(side=tk.RIGHT, pady=4)

        self.sim_loc_check = tk.Checkbutton(
            self.header_frame,
            text="Simulate Off-Campus [L]",
            variable=self.simulate_out_of_bounds,
            font=("Helvetica", 11),
            bg=self.default_bg,
            command=self.update_location_banner
        )
        self.sim_loc_check.pack(side=tk.RIGHT, padx=12, pady=4)

        # Live Geofence Status Bar
        self.location_label = tk.Label(
            self.main_frame,
            text="📍 Scanning Classroom Location...",
            font=("Helvetica", 12, "bold"),
            bg=self.default_bg
        )
        self.location_label.pack(pady=(0, 6))

        # Camera Display Container
        self.video_label = tk.Label(self.main_frame, bg="black", width=640, height=410)
        self.video_label.pack(pady=4)

        # Primary Status Banner
        self.status_label = tk.Label(
            self.main_frame,
            text="Hold your Student ID Card up to the camera",
            font=("Helvetica", 17, "bold"),
            bg=self.default_bg
        )
        self.status_label.pack(pady=(8, 4))

        # Student Profile & Shortcut Hint Label
        self.info_label = tk.Label(
            self.main_frame,
            text="Shortcuts: [Enter/Space] Confirm/Remove  •  [H] My Attendance  •  [T] Timetable  •  [L] Test GPS  •  [Esc] Rescan",
            font=("Helvetica", 12),
            bg=self.default_bg
        )
        self.info_label.pack(pady=(0, 4))

        # Active Timetable Period Banner
        self.subject_label = tk.Label(
            self.main_frame,
            text="",
            font=("Helvetica", 14, "bold"),
            bg=self.default_bg
        )
        self.subject_label.pack(pady=(0, 8))

        # Action Buttons
        self.btn_frame = tk.Frame(self.main_frame, bg=self.default_bg)
        self.btn_frame.pack(pady=6)

        self.confirm_btn = tk.Button(
            self.btn_frame,
            text="Confirm Attendance [Enter]",
            font=("Helvetica", 12, "bold"),
            width=21,
            height=2,
            state=tk.DISABLED,
            command=self.handle_primary_action
        )
        self.confirm_btn.grid(row=0, column=0, padx=5)

        self.history_btn = tk.Button(
            self.btn_frame,
            text="View My Attendance [H]",
            font=("Helvetica", 12, "bold"),
            width=19,
            height=2,
            state=tk.DISABLED,
            command=self.show_student_history_popup
        )
        self.history_btn.grid(row=0, column=1, padx=5)

        self.timetable_btn = tk.Button(
            self.btn_frame,
            text="View Timetable [T]",
            font=("Helvetica", 12, "bold"),
            width=16,
            height=2,
            command=self.show_timetable_popup
        )
        self.timetable_btn.grid(row=0, column=2, padx=5)

        self.rescan_btn = tk.Button(
            self.btn_frame,
            text="Rescan [Esc]",
            font=("Helvetica", 12),
            width=13,
            height=2,
            command=self.reset_scanner
        )
        self.rescan_btn.grid(row=0, column=3, padx=5)

        self.export_btn = tk.Button(
            self.btn_frame,
            text="Export Records to CSV",
            font=("Helvetica", 12),
            width=18,
            height=2,
            command=self.export_to_csv
        )
        self.export_btn.grid(row=0, column=4, padx=5)

        # Group widgets whose backgrounds dynamically change on scan
        self.color_reactive_widgets = [
            self.root,
            self.main_frame,
            self.header_frame,
            self.title_label,
            self.clock_label,
            self.sim_loc_check,
            self.location_label,
            self.status_label,
            self.info_label,
            self.subject_label,
            self.btn_frame
        ]

    def update_clock(self):
        """Display live date and time on the kiosk header."""
        now_str = datetime.now().strftime("%a, %d %b %Y  •  %I:%M:%S %p")
        self.clock_label.config(text=now_str)
        self.root.after(1000, self.update_clock)

    def set_theme_color(self, color):
        """Update the background color of the window and all labels."""
        for widget in self.color_reactive_widgets:
            widget.configure(bg=color)

    def play_beep(self):
        """Play native macOS sound asynchronously."""
        try:
            subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"])
        except Exception as e:
            print(f"Audio playback warning: {e}")

    def draw_alignment_guide(self, frame):
        """Draw a subtle viewfinder guide on the mirror feed to help students aim."""
        h, w, _ = frame.shape
        x1, y1 = int(w * 0.2), int(h * 0.2)
        x2, y2 = int(w * 0.8), int(h * 0.8)
        color = (220, 220, 220)
        length = 30
        thickness = 2

        cv2.line(frame, (x1, y1), (x1 + length, y1), color, thickness)
        cv2.line(frame, (x1, y1), (x1, y1 + length), color, thickness)
        cv2.line(frame, (x2, y1), (x2 - length, y1), color, thickness)
        cv2.line(frame, (x2, y1), (x2, y1 + length), color, thickness)
        cv2.line(frame, (x1, y2), (x1 + length, y2), color, thickness)
        cv2.line(frame, (x1, y2), (x1, y2 - length), color, thickness)
        cv2.line(frame, (x2, y2), (x2 - length, y2), color, thickness)
        cv2.line(frame, (x2, y2), (x2, y2 - length), color, thickness)

    def update_frame(self):
        """Continuously read from the webcam, decode on raw frame, and display mirrored feed."""
        if self.scanning and self.cap.isOpened():
            ret, raw_frame = self.cap.read()
            if ret:
                barcodes = decode(raw_frame)
                display_frame = cv2.flip(raw_frame, 1)

                if barcodes:
                    barcode_obj = barcodes[0]
                    barcode_data = barcode_obj.data.decode("utf-8").strip()

                    h, w, _ = raw_frame.shape
                    pts = barcode_obj.polygon
                    if len(pts) == 4:
                        mirrored_pts = [(w - pt.x, pt.y) for pt in pts]
                        for i in range(4):
                            cv2.line(
                                display_frame,
                                mirrored_pts[i],
                                mirrored_pts[(i + 1) % 4],
                                (0, 255, 0),
                                3
                            )

                    self.display_cv2_frame(display_frame)

                    self.play_beep()
                    self.scanning = False
                    self.validate_barcode(barcode_data)
                else:
                    self.draw_alignment_guide(display_frame)
                    self.display_cv2_frame(display_frame)

        self.root.after(30, self.update_frame)

    def display_cv2_frame(self, frame):
        """Convert OpenCV BGR frame to Tkinter-compatible PhotoImage."""
        frame_resized = cv2.resize(frame, (640, 410))
        rgb_frame = cv2.cvtColor(frame_resized, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(rgb_frame)
        tk_img = ImageTk.PhotoImage(image=pil_img)
        self.video_label.imgtk = tk_img
        self.video_label.configure(image=tk_img)

    def validate_barcode(self, barcode_data):
        """
        1. Look up student in database.
        2. Scan & verify student's physical location against classroom geofence.
        3. Match current class period and check for existing attendance.
        """
        if not self.ensure_db_connection():
            self.status_label.config(text="Database Error: Connection Unavailable")
            return

        try:
            cursor = self.db_conn.cursor(dictionary=True)

            # 1. Look up the barcode in the students table
            cursor.execute(
                "SELECT student_id, name, class_name, barcode FROM students WHERE barcode = %s",
                (barcode_data,)
            )
            student = cursor.fetchone()

            if not student:
                self.current_student = None
                self.current_period = None
                self.existing_attendance_id = None
                self.action_mode = None

                self.set_theme_color(COLOR_UNKNOWN)
                self.status_label.config(text="Unrecognised ID Card")
                self.info_label.config(
                    text=f"Code '{barcode_data}' is not registered in the student directory."
                )
                self.subject_label.config(text="")
                self.confirm_btn.config(text="Confirm Attendance [Enter]", state=tk.DISABLED)
                self.history_btn.config(state=tk.DISABLED)
                self.export_btn.config(text="Export Records to CSV")
                self.start_auto_reset_timer(AUTO_RESET_SECONDS)
                cursor.close()
                return

            # Student found -> Enable their personal Attendance Record viewer & Export
            self.current_student = student
            self.history_btn.config(state=tk.NORMAL)
            self.export_btn.config(text=f"Export {student['name'].split()[0]}'s CSV")

            # 2. Scan Location BEFORE allowing attendance to be marked
            location_ok, distance_m, coords_str = self.verify_student_location()
            if not location_ok:
                self.current_period = None
                self.existing_attendance_id = None
                self.action_mode = None

                self.set_theme_color(COLOR_DUPLICATE)
                self.status_label.config(
                    text="Location Mismatch — Attendance Cannot Be Marked!"
                )
                self.info_label.config(
                    text=f"Student: {student['name']} ({student['class_name']})   |   Detected {distance_m}m away from classroom"
                )
                self.subject_label.config(
                    text=f"You must be within {ALLOWED_RADIUS_METERS}m of the classroom to mark attendance."
                )
                self.confirm_btn.config(text="Location Out of Range", state=tk.DISABLED)
                self.start_auto_reset_timer(AUTO_RESET_SECONDS)
                cursor.close()
                return

            # 3. Look up the active period in the 24-hour timetable for their Branch/Class
            cursor.execute(
                """
                SELECT timetable_id, subject_name, start_time, end_time
                FROM timetable
                WHERE class_name = %s
                  AND CURTIME() BETWEEN start_time AND end_time
                LIMIT 1
                """,
                (student["class_name"],)
            )
            period = cursor.fetchone()
            self.current_period = period

            if not period:
                self.set_theme_color(COLOR_UNKNOWN)
                self.status_label.config(text="No Scheduled Class Right Now")
                self.info_label.config(
                    text=f"{student['name']}  •  Branch: {student['class_name']}  •  ID: {student['student_id']}"
                )
                self.subject_label.config(text="No active subject found in the timetable for your branch.")
                self.confirm_btn.config(text="Confirm Attendance [Enter]", state=tk.DISABLED)
                self.start_auto_reset_timer(AUTO_RESET_SECONDS)
                cursor.close()
                return

            start_fmt = format_mysql_time(period["start_time"])
            end_fmt = format_mysql_time(period["end_time"])

            # 4. Check if the student already logged attendance for THIS period today
            cursor.execute(
                """
                SELECT id, timestamp
                FROM attendance
                WHERE student_id = %s
                  AND timetable_id = %s
                  AND DATE(timestamp) = CURDATE()
                """,
                (student["student_id"], period["timetable_id"])
            )
            existing_record = cursor.fetchone()

            if existing_record:
                # Duplicate Scan -> Pastel Red + Offer to Remove Attendance
                self.existing_attendance_id = existing_record["id"]
                self.action_mode = "REMOVE"
                login_time_str = existing_record["timestamp"].strftime("%I:%M %p")

                self.set_theme_color(COLOR_DUPLICATE)
                self.status_label.config(
                    text=f"Already Logged for {period['subject_name']} (at {login_time_str})"
                )
                self.info_label.config(
                    text=f"Student: {student['name']}   |   Branch: {student['class_name']}   |   Location Verified ({distance_m}m)"
                )
                self.subject_label.config(
                    text="Click 'Remove Attendance' below (or press Enter) if you want to undo this check-in."
                )
                self.confirm_btn.config(text="Remove Attendance [Enter]", state=tk.NORMAL)
                self.start_auto_reset_timer(AUTO_RESET_SECONDS)
            else:
                # Valid & New Scan + Location Verified -> Pastel Green
                self.existing_attendance_id = None
                self.action_mode = "CONFIRM"

                self.set_theme_color(COLOR_VALID)
                self.status_label.config(text=f"Welcome, {student['name']}! Location & ID Verified")
                self.info_label.config(
                    text=f"Branch: {student['class_name']}   |   ID: {student['student_id']}   |   Location: {coords_str}"
                )
                self.subject_label.config(
                    text=f"Current Period: {period['subject_name']}  ({start_fmt} – {end_fmt})"
                )
                self.confirm_btn.config(text="Confirm Attendance [Enter]", state=tk.NORMAL)
                self.start_auto_reset_timer(AUTO_RESET_SECONDS)

            cursor.close()
        except Error as e:
            messagebox.showerror("Query Error", f"Failed to validate barcode:\n{e}")

    def handle_primary_action(self):
        """Route the primary button / Enter key to either Confirm or Remove attendance."""
        if self.confirm_btn["state"] == tk.DISABLED or not self.current_student:
            return

        if self.action_mode == "CONFIRM":
            self.confirm_attendance()
        elif self.action_mode == "REMOVE":
            self.remove_attendance()

    def confirm_attendance(self):
        """Re-verify location and insert the student's attendance record into MySQL."""
        if not self.current_student or not self.current_period:
            return

        # Final location check right as attendance is marked
        location_ok, distance_m, coords_str = self.verify_student_location()
        if not location_ok:
            self.set_theme_color(COLOR_DUPLICATE)
            self.status_label.config(text="Location Mismatch — Attendance Cannot Be Marked!")
            self.subject_label.config(
                text=f"Detected {distance_m}m away. Must be within {ALLOWED_RADIUS_METERS}m of classroom."
            )
            self.confirm_btn.config(text="Location Out of Range", state=tk.DISABLED)
            return

        if not self.ensure_db_connection():
            return

        self.cancel_auto_reset_timer()

        try:
            cursor = self.db_conn.cursor()
            cursor.execute(
                """
                INSERT INTO attendance (student_id, timetable_id, subject_name, location_coords)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    self.current_student["student_id"],
                    self.current_period["timetable_id"],
                    self.current_period["subject_name"],
                    coords_str
                )
            )
            self.db_conn.commit()
            cursor.close()

            now_time = datetime.now().strftime("%I:%M %p")
            self.status_label.config(
                text=f"Attendance Confirmed for {self.current_period['subject_name']} at {now_time}!"
            )
            self.confirm_btn.config(state=tk.DISABLED)
            self.action_mode = None

            self.root.after(1500, self.reset_scanner)
        except Error as e:
            messagebox.showerror("Database Error", f"Could not save attendance:\n{e}")

    def remove_attendance(self):
        """Delete the existing attendance log for the current student and period."""
        if not self.existing_attendance_id or not self.current_student:
            return

        if not self.ensure_db_connection():
            return

        self.cancel_auto_reset_timer()

        try:
            cursor = self.db_conn.cursor()
            cursor.execute(
                "DELETE FROM attendance WHERE id = %s",
                (self.existing_attendance_id,)
            )
            self.db_conn.commit()
            cursor.close()

            subj = self.current_period["subject_name"] if self.current_period else "current period"
            self.set_theme_color(self.default_bg)
            self.status_label.config(
                text=f"Attendance Removed for {self.current_student['name']} ({subj})"
            )
            self.subject_label.config(text="Your check-in for this period has been cancelled.")
            self.confirm_btn.config(state=tk.DISABLED)
            self.existing_attendance_id = None
            self.action_mode = None

            self.root.after(1500, self.reset_scanner)
        except Error as e:
            messagebox.showerror("Database Error", f"Could not remove attendance:\n{e}")

    def show_student_history_popup(self):
        """
        Open a modal window displaying the scanned student's personal attendance history.
        Uses Python datetime formatting to avoid MySQL '%' parameter placeholder collisions.
        """
        if not self.current_student or self.history_btn["state"] == tk.DISABLED:
            return

        if not self.ensure_db_connection():
            return

        self.cancel_auto_reset_timer()

        popup = tk.Toplevel(self.root)
        popup.title(f"Attendance Record — {self.current_student['name']} ({self.current_student['class_name']})")
        popup.geometry("760x440")
        popup.transient(self.root)
        popup.grab_set()

        header = tk.Label(
            popup,
            text=f"{self.current_student['name']}  •  Branch: {self.current_student['class_name']}  •  ID: {self.current_student['student_id']}",
            font=("Helvetica", 15, "bold"),
            pady=10
        )
        header.pack()

        table_frame = tk.Frame(popup, padx=15, pady=5)
        table_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("id", "subject", "date", "time", "location")
        tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=12)
        tree.heading("id", text="Log ID")
        tree.heading("subject", text="Subject / Period")
        tree.heading("date", text="Date")
        tree.heading("time", text="Check-In Time")
        tree.heading("location", text="Scanned Location")

        tree.column("id", width=60, anchor=tk.CENTER)
        tree.column("subject", width=230, anchor=tk.W)
        tree.column("date", width=110, anchor=tk.CENTER)
        tree.column("time", width=110, anchor=tk.CENTER)
        tree.column("location", width=190, anchor=tk.CENTER)

        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Fetch student's attendance logs without '%' format strings in SQL
        try:
            cursor = self.db_conn.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT id, subject_name, timestamp, location_coords
                FROM attendance
                WHERE student_id = %s
                ORDER BY timestamp DESC
                """,
                (self.current_student["student_id"],)
            )
            rows = cursor.fetchall()
            cursor.close()

            for row in rows:
                ts = row["timestamp"]
                att_date = ts.strftime("%d %b %Y") if isinstance(ts, datetime) else str(ts)
                att_time = ts.strftime("%I:%M:%S %p") if isinstance(ts, datetime) else str(ts)
                loc_str = row.get("location_coords") or "Verified Campus"
                tree.insert("", tk.END, values=(row["id"], row["subject_name"], att_date, att_time, loc_str))

            summary_text = f"Total Classes Attended: {len(rows)}"
        except Error as e:
            summary_text = f"Error loading records: {e}"

        footer_frame = tk.Frame(popup, pady=10)
        footer_frame.pack(fill=tk.X, padx=15)

        count_lbl = tk.Label(footer_frame, text=summary_text, font=("Helvetica", 12, "bold"))
        count_lbl.pack(side=tk.LEFT)

        def close_popup():
            popup.destroy()
            if self.current_student is not None:
                self.start_auto_reset_timer(AUTO_RESET_SECONDS)

        close_btn = tk.Button(footer_frame, text="Close [Esc]", width=12, command=close_popup)
        close_btn.pack(side=tk.RIGHT)
        popup.bind("<Escape>", lambda e: close_popup())
        popup.protocol("WM_DELETE_WINDOW", close_popup)

    def show_timetable_popup(self):
        """
        Open a modal window with tabs for each branch (CSE-A, ECE-A, MECH-A)
        showing the full 24-hour schedule and highlighting the currently active period.
        """
        if not self.ensure_db_connection():
            return

        self.cancel_auto_reset_timer()
        was_scanning = self.scanning
        self.scanning = False

        popup = tk.Toplevel(self.root)
        popup.title("24-Hour Branch Timetable — All Classes")
        popup.geometry("740x440")
        popup.transient(self.root)
        popup.grab_set()

        now_time_str = datetime.now().strftime("%I:%M %p")
        header = tk.Label(
            popup,
            text=f"24-Hour Class Timetable   •   Current Time: {now_time_str}",
            font=("Helvetica", 15, "bold"),
            pady=10
        )
        header.pack()

        notebook = ttk.Notebook(popup)
        notebook.pack(fill=tk.BOTH, expand=True, padx=15, pady=5)

        branches = ["CSE-A", "ECE-A", "MECH-A"]

        try:
            cursor = self.db_conn.cursor(dictionary=True)

            for branch in branches:
                tab_frame = tk.Frame(notebook, padx=10, pady=10)
                notebook.add(tab_frame, text=f"  {branch}  ")

                columns = ("slot", "subject", "status")
                tree = ttk.Treeview(tab_frame, columns=columns, show="headings", height=8)
                tree.heading("slot", text="Time Slot")
                tree.heading("subject", text="Subject / Course")
                tree.heading("status", text="Current Status")

                tree.column("slot", width=210, anchor=tk.CENTER)
                tree.column("subject", width=310, anchor=tk.W)
                tree.column("status", width=150, anchor=tk.CENTER)

                tree.tag_configure("active_slot", background=COLOR_VALID, font=("Helvetica", 12, "bold"))
                tree.pack(fill=tk.BOTH, expand=True)

                cursor.execute(
                    """
                    SELECT subject_name, start_time, end_time,
                           (CURTIME() BETWEEN start_time AND end_time) AS is_active
                    FROM timetable
                    WHERE class_name = %s
                    ORDER BY start_time ASC
                    """,
                    (branch,)
                )
                periods = cursor.fetchall()

                for p in periods:
                    start_fmt = format_mysql_time(p["start_time"])
                    end_fmt = format_mysql_time(p["end_time"])
                    slot_str = f"{start_fmt}  –  {end_fmt}"
                    if p["is_active"]:
                        status_str = "🟢 ACTIVE NOW"
                        tree.insert("", tk.END, values=(slot_str, p["subject_name"], status_str), tags=("active_slot",))
                    else:
                        status_str = "Scheduled"
                        tree.insert("", tk.END, values=(slot_str, p["subject_name"], status_str))

            cursor.close()

            if self.current_student and self.current_student["class_name"] in branches:
                target_idx = branches.index(self.current_student["class_name"])
                notebook.select(target_idx)

        except Error as e:
            messagebox.showerror("Database Error", f"Could not load timetable:\n{e}")

        footer_frame = tk.Frame(popup, pady=10)
        footer_frame.pack(fill=tk.X, padx=15)

        hint_lbl = tk.Label(
            footer_frame,
            text="Green row indicates the active class period for check-in right now.",
            font=("Helvetica", 11)
        )
        hint_lbl.pack(side=tk.LEFT)

        def close_popup():
            popup.destroy()
            if was_scanning:
                self.scanning = True
            elif self.current_student is not None:
                self.start_auto_reset_timer(AUTO_RESET_SECONDS)

        close_btn = tk.Button(footer_frame, text="Close [Esc]", width=12, command=close_popup)
        close_btn.pack(side=tk.RIGHT)
        popup.bind("<Escape>", lambda e: close_popup())
        popup.protocol("WM_DELETE_WINDOW", close_popup)

    def start_auto_reset_timer(self, seconds):
        """Start a visual countdown so the kiosk resets automatically if a student walks away."""
        self.cancel_auto_reset_timer()
        self.countdown_remaining = seconds
        self.tick_auto_reset()

    def tick_auto_reset(self):
        """Update the Rescan button with the remaining seconds before auto-reset."""
        if self.countdown_remaining > 0:
            self.rescan_btn.config(text=f"Rescan ({self.countdown_remaining}s)")
            self.countdown_remaining -= 1
            self.auto_reset_job = self.root.after(1000, self.tick_auto_reset)
        else:
            self.reset_scanner()

    def cancel_auto_reset_timer(self):
        """Cancel any active auto-reset timer."""
        if self.auto_reset_job is not None:
            self.root.after_cancel(self.auto_reset_job)
            self.auto_reset_job = None
        self.rescan_btn.config(text="Rescan [Esc]")

    def reset_scanner(self):
        """Reset visual feedback to default system color and resume camera scanning."""
        self.cancel_auto_reset_timer()
        self.current_student = None
        self.current_period = None
        self.existing_attendance_id = None
        self.action_mode = None

        self.set_theme_color(self.default_bg)
        self.update_location_banner()
        self.status_label.config(text="Hold your Student ID Card up to the camera")
        self.info_label.config(
            text="Shortcuts: [Enter/Space] Confirm/Remove  •  [H] My Attendance  •  [T] Timetable  •  [L] Test GPS  •  [Esc] Rescan"
        )
        self.subject_label.config(text="")
        self.confirm_btn.config(text="Confirm Attendance [Enter]", state=tk.DISABLED)
        self.history_btn.config(state=tk.DISABLED)
        self.export_btn.config(text="Export Records to CSV")
        self.scanning = True

    def export_to_csv(self):
        """Pause scanning and export attendance records (including branch, subject & location) to CSV."""
        self.cancel_auto_reset_timer()
        was_scanning = self.scanning
        self.scanning = False

        if self.current_student:
            default_filename = f"attendance_{self.current_student['student_id']}_{self.current_student['name'].replace(' ', '_')}.csv"
            dialog_title = f"Export {self.current_student['name']}'s Attendance History"
        else:
            default_filename = "all_attendance_records.csv"
            dialog_title = "Export Attendance Records"

        file_path = filedialog.asksaveasfilename(
            title=dialog_title,
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")],
            initialfile=default_filename
        )

        if file_path and self.ensure_db_connection():
            try:
                cursor = self.db_conn.cursor()
                if self.current_student:
                    query = """
                        SELECT 
                            a.id AS Attendance_ID,
                            s.student_id AS Student_ID,
                            s.name AS Student_Name,
                            s.class_name AS Branch,
                            a.subject_name AS Subject,
                            a.location_coords AS Location,
                            a.timestamp AS Check_In_Timestamp
                        FROM attendance a
                        INNER JOIN students s ON a.student_id = s.student_id
                        WHERE s.student_id = %s
                        ORDER BY a.timestamp DESC
                    """
                    cursor.execute(query, (self.current_student["student_id"],))
                else:
                    query = """
                        SELECT 
                            a.id AS Attendance_ID,
                            s.student_id AS Student_ID,
                            s.name AS Student_Name,
                            s.class_name AS Branch,
                            a.subject_name AS Subject,
                            a.location_coords AS Location,
                            a.timestamp AS Check_In_Timestamp
                        FROM attendance a
                        INNER JOIN students s ON a.student_id = s.student_id
                        ORDER BY a.timestamp DESC
                    """
                    cursor.execute(query)

                records = cursor.fetchall()
                headers = [col[0] for col in cursor.description]

                with open(file_path, mode="w", newline="", encoding="utf-8") as csv_file:
                    writer = csv.writer(csv_file)
                    writer.writerow(headers)
                    writer.writerows(records)

                cursor.close()
                messagebox.showinfo(
                    "Export Complete",
                    f"Successfully exported {len(records)} attendance record(s) to:\n{file_path}"
                )
            except Exception as e:
                messagebox.showerror("Export Failed", f"An error occurred while exporting:\n{e}")

        if was_scanning:
            self.scanning = True
        elif self.current_student is not None:
            self.start_auto_reset_timer(AUTO_RESET_SECONDS)

    def on_closing(self):
        """Release webcam and database resources cleanly."""
        self.cancel_auto_reset_timer()
        self.scanning = False
        if self.cap.isOpened():
            self.cap.release()
        if self.db_conn and self.db_conn.is_connected():
            self.db_conn.close()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = StudentAttendanceKiosk(root)
    root.mainloop()