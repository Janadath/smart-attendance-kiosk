Project Report: Smart Attendance Kiosk with Geofencing

Table of Contents
1. Abstract
2. System Architecture & Technologies
3. Database Design
4. Key Features & Implementation
5. GitHub Repository & Download Instructions

1. Abstract
The Smart Attendance Kiosk with Geofencing is an automated, self-service desktop application designed to streamline student attendance tracking and decisively eliminate proxy attendance. Traditional attendance systems—whether manual roll calls or basic RFID card taps—often suffer from human error, time inefficiencies, and the inability to verify a student's actual physical presence in the classroom. This project addresses these critical flaws by introducing a robust software kiosk built on Python, OpenCV, and MySQL, pairing rapid QR code and 1D Barcode scanning with live geographical location validation.

At the core of the system is a dynamic 24-hour timetable integration and a geofencing engine. When a student scans their ID, the kiosk fetches the device's live network coordinates via IP geolocation and calculates the exact great-circle distance to the designated campus classroom using the Haversine formula. If the student is outside the permissible 500-meter radius, the system instantly blocks the check-in. Furthermore, the application maps the student's enrolled branch against a continuous 24-hour database schedule to intelligently identify the active subject and period, preventing off-hours logging and cross-branch errors.

To maximize throughput and ensure an intuitive user experience, the kiosk interface is fully student-centric. It features a mirrored webcam feed to naturally guide ID card placement and delivers instantaneous color-coded visual cues—Pastel Green for verified check-ins, Pastel Red for duplicate scans or out-of-range errors, and Pastel Orange for unregistered barcodes. Complemented by non-blocking native system audio feedback, instant check-in reversals, and hands-free keyboard shortcuts, the application operates as a high-speed, secure, and privacy-conscious terminal for modern educational environments.

2. System Architecture & Technologies
The project is developed using a robust and lightweight technology stack:
* Programming Language: Python 3
* GUI Framework: Tkinter with Pillow (PIL)
* Computer Vision: OpenCV (cv2) and pyzbar for high-speed scanning
* Database Management: MySQL 8.4 (via mysql-connector-python)
* Location Engine: IP Geolocation via ipinfo.io and Haversine distance calculations
* Audio Feedback: Native system sound subprocesses

3. Database Design
The system relies on a normalized relational database consisting of three core tables:
1. students: Stores student identity, branch (class_name), and unique barcode.
2. timetable: Holds the 24-hour schedule for every branch, defining start and end times for each subject.
3. attendance: Logs every successful scan with timestamps, linked subject_name, and location_coords.

4. Key Features & Implementation
* Automated Database Setup: A standalone Python script (setup_database.py) simplifies deployment by automatically creating the required database schema, tables, 24-hour timetable, and sample student data on any new machine.
* Geofencing Validation: Calculates the great-circle distance between the kiosk and the student's network location. If the distance exceeds the 500m threshold, attendance is blocked.
* Dynamic Timetable Matching: Matches the student's branch against the active timetable slot in real-time, preventing attendance logging during off-hours.
* Student-Centric Interface: Features a mirrored webcam feed, color-coded visual feedback (Green for valid, Red for duplicate/out-of-range), and hands-free keyboard shortcuts.
* Instant Reversal: Allows students to instantly revoke or remove their check-in if they double-scan within the same period.
* Export and Reporting: Includes a direct CSV export utility to download attendance logs.

5. GitHub Repository & Download Instructions
The source code and setup instructions are hosted on GitHub. To download and run the project, follow these steps:
Repository Link: https://github.com/YourUsername/smart-attendance-kiosk
(Replace 'YourUsername' with your actual GitHub username.)

Step-by-step Download & Setup:
1. Clone the repository to your local machine:
git clone https://github.com/YourUsername/smart-attendance-kiosk.git

2. Navigate to the project directory:
cd smart-attendance-kiosk

3. Install the required Python dependencies:
pip install opencv-python pillow pyzbar mysql-connector-python python-barcode qrcode

4. Configure the Database:
Open the setup_database.py file and input your local MySQL credentials. Run 'python setup_database.py' to automatically construct the database, tables, and insert sample data.

5. Run the Application:
Generate the student ID cards by running 'python generate_id_cards.py', then launch the kiosk using 'python attendance_app.py'.