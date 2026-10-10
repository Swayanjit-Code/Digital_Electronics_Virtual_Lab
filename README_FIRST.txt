VIRTUAL LAB - UPDATED EXISTING PROJECT

IMPORTANT DATABASE SAFETY:
- labportal.db in this package is a copy of the database provided for this task.
- The original uploaded database was not edited by the update process.
- app.py intentionally does not automatically initialize or migrate the database.
- Keep a separate backup of your original project before copying these files into your own folder.

PROJECT STRUCTURE:
app.py
labportal.db
requirements.txt
templates/ (all 7 HTML templates)
uploads/experiments/
uploads/reports/
backups/

NOTE:
The uploads folders are empty in this package because the PDFs in your screenshot were local files not provided as attachments. Uploads from your own PC are not included here.

Run locally (Windows terminal, from this folder):
  py -m venv venv
  venv\Scripts\activate
  pip install -r requirements.txt
  py app.py

This is a working draft, not yet fully integration-tested. Verify login, dashboards, report upload/grading, and simulation interactions locally before deploying.
