"""
usercontroller.py — Standalone PyQt6 User Management Tool
=========================================================
Direct PostgreSQL connection — no server required.

Features:
- List all users
- Add new user
- Edit user (username, password, is_active, is_admin)
- Delete user
- View and manage WebAuthn passkeys per user

Usage:
    python usercontroller.py

Environment variables (or config.env):
    DB_HOST, DB_PORT, DB_NAME, DB_USER, POSTGRES_PASSWORD
"""

import os
import sys
import uuid
import bcrypt
from datetime import datetime
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QPushButton, QDialog, QLabel,
    QLineEdit, QCheckBox, QMessageBox, QHeaderView, QGroupBox, QFormLayout,
    QScrollArea, QFrame
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor

# -----------------------------------------------------------------------
# Database Connection (Direct PostgreSQL)
# -----------------------------------------------------------------------
try:
    import psycopg2
    from psycopg2.extras import DictCursor
except ImportError:
    print("Error: psycopg2 is required. Install with: pip install psycopg2-binary")
    sys.exit(1)


def load_env():
    """Load environment variables from config.env if present."""
    base = os.path.dirname(os.path.abspath(__file__))
    env_file = os.path.join(base, 'config.env')
    if os.path.isfile(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, _, v = line.partition('=')
                    os.environ.setdefault(k.strip(), v.strip())


def get_db_connection():
    """Create a direct PostgreSQL connection."""
    load_env()
    return psycopg2.connect(
        host=os.environ.get('DB_HOST', 'localhost'),
        port=os.environ.get('DB_PORT', '5432'),
        database=os.environ.get('DB_NAME', 'sdr_db'),
        user=os.environ.get('DB_USER', 'postgres'),
        password=os.environ.get('POSTGRES_PASSWORD', 'changeme'),
        cursor_factory=DictCursor
    )


# -----------------------------------------------------------------------
# User CRUD Operations
# -----------------------------------------------------------------------
def list_users():
    """Fetch all users from database."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, username, is_active, is_admin, created_at
                FROM users
                ORDER BY created_at DESC
            """)
            return cur.fetchall()
    finally:
        conn.close()


def create_user(username, password, is_active=True, is_admin=False):
    """Create a new user with bcrypt password hashing."""
    conn = get_db_connection()
    try:
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO users (id, username, password_hash, is_active, is_admin, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (str(uuid.uuid4()), username, password_hash, is_active, is_admin, datetime.utcnow()))
            conn.commit()
        return True
    except Exception as e:
        print(f"Error creating user: {e}")
        return False
    finally:
        conn.close()


def update_user(user_id, username=None, password=None, is_active=None, is_admin=None):
    """Update an existing user."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            updates = []
            params = []
            
            if username is not None:
                updates.append("username = %s")
                params.append(username)
            
            if password is not None:
                password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()
                updates.append("password_hash = %s")
                params.append(password_hash)
            
            if is_active is not None:
                updates.append("is_active = %s")
                params.append(is_active)
            
            if is_admin is not None:
                updates.append("is_admin = %s")
                params.append(is_admin)
            
            if updates:
                params.append(user_id)
                cur.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = %s", params)
                conn.commit()
                return True
            return False
    except Exception as e:
        print(f"Error updating user: {e}")
        return False
    finally:
        conn.close()


def delete_user(user_id):
    """Delete a user by ID."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
            conn.commit()
            return True
    except Exception as e:
        print(f"Error deleting user: {e}")
        return False
    finally:
        conn.close()


def list_passkeys(user_id):
    """Fetch all passkeys for a user."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, credential_id, device_name, created_at, last_login_at, last_login_ip, last_user_agent
                FROM passkey_credentials
                WHERE user_id = %s
                ORDER BY created_at DESC
            """, (user_id,))
            return cur.fetchall()
    finally:
        conn.close()


def delete_passkey(credential_id):
    """Delete a passkey by credential_id."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM passkey_credentials WHERE credential_id = %s", (credential_id,))
            conn.commit()
            return True
    except Exception as e:
        print(f"Error deleting passkey: {e}")
        return False
    finally:
        conn.close()


# -----------------------------------------------------------------------
# PyQt6 UI Components
# -----------------------------------------------------------------------
class UserDialog(QDialog):
    """Dialog for adding/editing users."""
    
    def __init__(self, parent=None, user_data=None):
        super().__init__(parent)
        self.user_data = user_data
        self.setWindowTitle("Add User" if user_data is None else "Edit User")
        self.setModal(True)
        self.setMinimumWidth(400)
        self.setup_ui()
    
    def setup_ui(self):
        layout = QVBoxLayout()
        
        form_layout = QFormLayout()
        
        # Username
        self.username_input = QLineEdit()
        if self.user_data:
            self.username_input.setText(self.user_data['username'])
        form_layout.addRow("Username:", self.username_input)
        
        # Password
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("Leave blank to keep current password" if self.user_data else "Enter password")
        form_layout.addRow("Password:", self.password_input)
        
        # Is Active
        self.is_active_check = QCheckBox("Active")
        self.is_active_check.setChecked(True if self.user_data is None else self.user_data['is_active'])
        form_layout.addRow("", self.is_active_check)
        
        # Is Admin
        self.is_admin_check = QCheckBox("Admin")
        self.is_admin_check.setChecked(False if self.user_data is None else self.user_data['is_admin'])
        form_layout.addRow("", self.is_admin_check)
        
        layout.addLayout(form_layout)
        
        # Buttons
        button_layout = QHBoxLayout()
        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self.accept)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.save_btn)
        button_layout.addWidget(self.cancel_btn)
        layout.addLayout(button_layout)
        
        self.setLayout(layout)
    
    def get_data(self):
        """Return the form data."""
        return {
            'username': self.username_input.text().strip(),
            'password': self.password_input.text(),
            'is_active': self.is_active_check.isChecked(),
            'is_admin': self.is_admin_check.isChecked()
        }


class PasskeyDialog(QDialog):
    """Dialog to view and manage passkeys for a user."""
    
    def __init__(self, parent=None, user_id=None, username=None):
        super().__init__(parent)
        self.user_id = user_id
        self.username = username
        self.setWindowTitle(f"Passkeys for {username}")
        self.setModal(True)
        self.setMinimumSize(700, 500)
        self.setup_ui()
        self.load_passkeys()
    
    def setup_ui(self):
        layout = QVBoxLayout()
        
        # Info label
        info = QLabel(f"Manage WebAuthn passkeys for user: {self.username}")
        info.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(info)
        
        # Passkey table
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Device Name", "Created", "Last Login", "Last IP", "Actions"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        
        # Buttons
        button_layout = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.load_passkeys)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.accept)
        button_layout.addWidget(self.refresh_btn)
        button_layout.addStretch()
        button_layout.addWidget(self.close_btn)
        layout.addLayout(button_layout)
        
        self.setLayout(layout)
    
    def load_passkeys(self):
        """Load passkeys from database."""
        try:
            passkeys = list_passkeys(self.user_id)
            self.table.setRowCount(0)
            
            if not passkeys:
                self.table.insertRow(0)
                no_data = QTableWidgetItem("No passkeys registered")
                no_data.setForeground(QColor("gray"))
                self.table.setItem(0, 0, no_data)
                for col in range(1, 5):
                    self.table.setItem(0, col, QTableWidgetItem(""))
                return
            
            for row, pk in enumerate(passkeys):
                self.table.insertRow(row)
                
                device_name = pk['device_name'] or 'Unknown Device'
                self.table.setItem(row, 0, QTableWidgetItem(device_name))
                
                created = pk['created_at'].strftime("%Y-%m-%d %H:%M") if pk['created_at'] else "N/A"
                self.table.setItem(row, 1, QTableWidgetItem(created))
                
                last_login = pk['last_login_at'].strftime("%Y-%m-%d %H:%M") if pk['last_login_at'] else "Never"
                self.table.setItem(row, 2, QTableWidgetItem(last_login))
                
                last_ip = pk['last_login_ip'] or "N/A"
                self.table.setItem(row, 3, QTableWidgetItem(last_ip))
                
                # Delete button
                delete_btn = QPushButton("Remove")
                delete_btn.clicked.connect(lambda checked, cred_id=pk['credential_id'], dev=device_name: self.delete_passkey(cred_id, dev))
                self.table.setCellWidget(row, 4, delete_btn)
                
                # Store credential_id for reference
                self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, pk['credential_id'])
        
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load passkeys: {e}")
    
    def delete_passkey(self, credential_id, device_name):
        """Delete a passkey with confirmation."""
        reply = QMessageBox.question(
            self, "Confirm Remove",
            f"Remove passkey '{device_name}'?\n\nThe user will need to re-register their device to login.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            if delete_passkey(credential_id):
                QMessageBox.information(self, "Success", "Passkey removed successfully")
                self.load_passkeys()
            else:
                QMessageBox.critical(self, "Error", "Failed to remove passkey")


class UserControllerWindow(QMainWindow):
    """Main window for user management."""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CGrep User Controller")
        self.setMinimumSize(900, 600)
        self.setup_ui()
        self.load_users()
    
    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        
        # Title
        title = QLabel("User Management")
        title.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(title)
        
        # Button row
        button_layout = QHBoxLayout()
        
        self.add_btn = QPushButton("Add User")
        self.add_btn.clicked.connect(self.add_user)
        self.add_btn.setMinimumHeight(40)
        
        self.edit_btn = QPushButton("Edit User")
        self.edit_btn.clicked.connect(self.edit_user)
        self.edit_btn.setMinimumHeight(40)
        self.edit_btn.setEnabled(False)
        
        self.delete_btn = QPushButton("Delete User")
        self.delete_btn.clicked.connect(self.delete_user)
        self.delete_btn.setMinimumHeight(40)
        self.delete_btn.setEnabled(False)
        
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.load_users)
        self.refresh_btn.setMinimumHeight(40)
        
        self.manage_passkeys_btn = QPushButton("Manage Passkeys")
        self.manage_passkeys_btn.clicked.connect(self.manage_passkeys)
        self.manage_passkeys_btn.setMinimumHeight(40)
        self.manage_passkeys_btn.setEnabled(False)
        
        button_layout.addWidget(self.add_btn)
        button_layout.addWidget(self.edit_btn)
        button_layout.addWidget(self.delete_btn)
        button_layout.addWidget(self.manage_passkeys_btn)
        button_layout.addStretch()
        button_layout.addWidget(self.refresh_btn)
        main_layout.addLayout(button_layout)
        
        # User table
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["ID", "Username", "Active", "Admin", "Passkeys", "Created At"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self.on_selection_changed)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        main_layout.addWidget(self.table)
    
    def load_users(self):
        """Load users from database into table."""
        try:
            users = list_users()
            self.table.setRowCount(0)
            
            for row, user in enumerate(users):
                self.table.insertRow(row)
                
                self.table.setItem(row, 0, QTableWidgetItem(str(user['id'])[:8] + "..."))
                self.table.setItem(row, 1, QTableWidgetItem(user['username']))
                
                active_item = QTableWidgetItem("Yes" if user['is_active'] else "No")
                active_item.setForeground(QColor("green" if user['is_active'] else "red"))
                self.table.setItem(row, 2, active_item)
                
                admin_item = QTableWidgetItem("Yes" if user['is_admin'] else "No")
                admin_item.setForeground(QColor("blue" if user['is_admin'] else QColor("gray")))
                self.table.setItem(row, 3, admin_item)
                
                # Count passkeys for this user
                conn = get_db_connection()
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT COUNT(*) FROM passkey_credentials WHERE user_id = %s", (str(user['id']),))
                        passkey_count = cur.fetchone()['count']
                finally:
                    conn.close()
                
                passkey_item = QTableWidgetItem(str(passkey_count))
                if passkey_count > 0:
                    passkey_item.setForeground(QColor("green"))
                else:
                    passkey_item.setForeground(QColor("gray"))
                self.table.setItem(row, 4, passkey_item)
                
                created = user['created_at'].strftime("%Y-%m-%d %H:%M") if user['created_at'] else "N/A"
                self.table.setItem(row, 5, QTableWidgetItem(created))
                
                # Store full user ID in hidden data
                self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, str(user['id']))
                self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole + 1, user)
        
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load users: {e}")
    
    def on_selection_changed(self):
        """Enable/disable buttons based on selection."""
        has_selection = self.table.currentRow() >= 0
        self.edit_btn.setEnabled(has_selection)
        self.delete_btn.setEnabled(has_selection)
        self.manage_passkeys_btn.setEnabled(has_selection)
    
    def add_user(self):
        """Open dialog to add a new user."""
        dialog = UserDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            data = dialog.get_data()
            
            if not data['username']:
                QMessageBox.warning(self, "Warning", "Username is required")
                return
            
            if not data['password']:
                QMessageBox.warning(self, "Warning", "Password is required for new users")
                return
            
            if create_user(data['username'], data['password'], data['is_active'], data['is_admin']):
                QMessageBox.information(self, "Success", "User created successfully")
                self.load_users()
            else:
                QMessageBox.critical(self, "Error", "Failed to create user")
    
    def edit_user(self):
        """Open dialog to edit selected user."""
        row = self.table.currentRow()
        if row < 0:
            return
        
        user_data = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole + 1)
        user_id = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        
        dialog = UserDialog(self, user_data)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            data = dialog.get_data()
            
            if not data['username']:
                QMessageBox.warning(self, "Warning", "Username is required")
                return
            
            # Determine what to update
            kwargs = {'username': data['username']}
            if data['password']:
                kwargs['password'] = data['password']
            kwargs['is_active'] = data['is_active']
            kwargs['is_admin'] = data['is_admin']
            
            if update_user(user_id, **kwargs):
                QMessageBox.information(self, "Success", "User updated successfully")
                self.load_users()
            else:
                QMessageBox.critical(self, "Error", "Failed to update user")
    
    def delete_user(self):
        """Delete selected user with confirmation."""
        row = self.table.currentRow()
        if row < 0:
            return
        
        username = self.table.item(row, 1).text()
        user_id = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        
        reply = QMessageBox.question(
            self, "Confirm Delete",
            f"Are you sure you want to delete user '{username}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            if delete_user(user_id):
                QMessageBox.information(self, "Success", "User deleted successfully")
                self.load_users()
            else:
                QMessageBox.critical(self, "Error", "Failed to delete user")
    
    def manage_passkeys(self):
        """Open dialog to manage passkeys for selected user."""
        row = self.table.currentRow()
        if row < 0:
            return
        
        username = self.table.item(row, 1).text()
        user_id = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        
        dialog = PasskeyDialog(self, user_id, username)
        dialog.exec()
        self.load_users()  # Refresh to update passkey count


# -----------------------------------------------------------------------
# Main Entry Point
# -----------------------------------------------------------------------
def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    window = UserControllerWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
