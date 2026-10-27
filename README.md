# Smart Attendance Kiosk with Geofencing

A Python-based, self-service student attendance kiosk that uses OpenCV and `pyzbar` for barcode/QR code scanning. Designed to prevent proxy attendance, it features real-time geofencing to ensure students are physically present on campus, along with a dynamic 24-hour timetable system that automatically maps scans to the correct subject and period.

## Features

* **Proxy-Free Geofencing:** Verifies the device's live location against campus coordinates before allowing attendance to be marked.
* **Dynamic 24-Hour Timetable:** Automatically fetches the ongoing subject for the student's branch (e.g., CSE-A, ECE-A) based on the current time.
* **Self-Service Kiosk UI:** A mirrored webcam feed with intuitive keyboard shortcuts (Enter, Esc, H, T) designed for quick, hands-free operation.
* **Smart Visual Feedback:** The UI dynamically changes colour to indicate success (Pastel Green), duplicate/geofence errors (Pastel Red), or unrecognised cards (Pastel Orange).
* **Live Attendance & Timetable Viewer:** Students can view their personal check-in history and the full branch timetable directly from the kiosk.
* **Automated ID Card Generation:** Includes a companion script to instantly generate printable A4 sheets of student ID cards containing both QR and Code-128 barcodes.
* **CSV Export:** Export global attendance records or individual student histories.

## Technology Stack

* **Language:** Python 3
* **GUI:** Tkinter & Pillow (PIL)
* **Computer Vision & Scanning:** OpenCV (`cv2`) & `pyzbar`
* **Database:** MySQL 8.4 (`mysql-connector-python`)
* **Audio:** Native macOS `afplay` subprocess
* **Location Engine:** Built-in `urllib` (IP Geolocation via ipinfo.io) & Haversine distance formula

## Prerequisites

1. **MySQL 8.4:** Installed and running locally or on a cloud server.
2. **Python Packages:** 
   ```bash
   pip install opencv-python pillow pyzbar mysql-connector-python python-barcode qrcode
   ```
3. **ZBar Library (macOS):** Required for `pyzbar` to decode barcodes.
   ```bash
   brew install zbar
   ```

## Installation & Setup

**1. Clone the repository**
```bash
git clone https://github.com/YourUsername/smart-attendance-kiosk.git
cd smart-attendance-kiosk
```

**2. Set up the MySQL Database**
Run the provided SQL setup queries (found in the project documentation) in your MySQL environment to create the `smart_attendance` database, along with the `students`, `timetable`, and `attendance` tables.

**3. Configure the Application**
Open `attendance_app.py` and `generate_id_cards.py` and update the `DB_CONFIG` dictionary with your MySQL credentials:
```python
DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "your_password",
    "database": "smart_attendance",
    "port": 3306
}
```

*Optional Geofencing Setup:* In `attendance_app.py`, update `CAMPUS_LAT` and `CAMPUS_LON` with your exact classroom GPS coordinates. If left as `None`, the application will automatically calibrate to the Mac's current location upon launch.

## Usage

**1. Generate Student ID Cards**
Before running the scanner, generate the test ID cards based on the students in your database.
```bash
python generate_id_cards.py
```
This will create a `student_id_cards/` folder containing individual PNG cards and a combined `printable_id_cards.pdf`.

**2. Start the Attendance Kiosk**
Launch the main application:
```bash
python attendance_app.py
```

### Kiosk Keyboard Shortcuts
| Shortcut | Action |
| :--- | :--- |
| `[Enter]` or `[Space]` | Confirm attendance (or Remove attendance if already scanned) |
| `[H]` | View personal attendance history popup |
| `[T]` | View the 24-hour branch timetable |
| `[L]` | Toggle "Simulate Off-Campus" mode to test geofence rejections |
| `[Esc]` | Rescan / Clear current student profile |

## License
This project is open-source and available under the MIT License.