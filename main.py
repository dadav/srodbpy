import sys
import time
import json
import os
import mssql_python
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QCheckBox,
    QMessageBox,
    QFormLayout,
    QProgressBar,
    QDialog,
    QDialogButtonBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal


class DatabaseSettingsDialog(QDialog):
    """Dialog for configuring database connection settings."""

    def __init__(self, parent=None, current_settings=None):
        super().__init__(parent)
        self.setWindowTitle("Database Connection Settings")
        self.setModal(True)

        layout = QVBoxLayout(self)
        form_layout = QFormLayout()

        # Server
        self.server_input = QLineEdit(current_settings.get("server", "localhost"))
        form_layout.addRow("Server:", self.server_input)

        # Port
        self.port_input = QLineEdit(str(current_settings.get("port", 1433)))
        form_layout.addRow("Port:", self.port_input)

        # Database
        self.database_input = QLineEdit(
            current_settings.get("database", "SRO_VT_SHARD")
        )
        form_layout.addRow("Database:", self.database_input)

        # User
        self.user_input = QLineEdit(current_settings.get("user", "sa"))
        form_layout.addRow("User:", self.user_input)

        # Password
        self.password_input = QLineEdit(current_settings.get("password", ""))
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        form_layout.addRow("Password:", self.password_input)

        layout.addLayout(form_layout)

        # Buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def get_settings(self):
        """Return the configured settings."""
        return {
            "server": self.server_input.text().strip(),
            "port": int(self.port_input.text().strip()),
            "database": self.database_input.text().strip(),
            "user": self.user_input.text().strip(),
            "password": self.password_input.text(),
        }


class BackupWorker(QThread):
    """Worker thread to create backup."""

    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, db_config):
        super().__init__()
        self.db_config = db_config

    def run(self):
        """Create backup of drop table."""
        try:
            conn = mssql_python.connect(self.db_config)
            cursor = conn.cursor()

            self.progress.emit("Creating backup...")

            # Drop backup table if it exists
            cursor.execute("""
                IF OBJECT_ID('_RefMonster_AssignedItemDrop_Backup', 'U') IS NOT NULL
                    DROP TABLE _RefMonster_AssignedItemDrop_Backup
            """)

            # Create backup table
            cursor.execute("""
                SELECT *
                INTO _RefMonster_AssignedItemDrop_Backup
                FROM _RefMonster_AssignedItemDrop
            """)

            # Count rows
            cursor.execute("SELECT COUNT(*) FROM _RefMonster_AssignedItemDrop_Backup")
            count = cursor.fetchone()[0]

            conn.commit()
            conn.close()

            self.finished.emit(
                True, f"Backup created successfully!\n{count} rows backed up."
            )

        except Exception as e:
            self.finished.emit(False, f"Backup failed: {str(e)}")


class RestoreWorker(QThread):
    """Worker thread to restore from backup."""

    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, db_config):
        super().__init__()
        self.db_config = db_config

    def run(self):
        """Restore drop table from backup."""
        try:
            conn = mssql_python.connect(self.db_config)
            cursor = conn.cursor()

            self.progress.emit("Restoring from backup...")

            # Check if backup exists
            cursor.execute("""
                SELECT COUNT(*)
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_NAME = '_RefMonster_AssignedItemDrop_Backup'
            """)
            if cursor.fetchone()[0] == 0:
                raise Exception("No backup found! Please create a backup first.")

            # Clear current table
            cursor.execute("DELETE FROM _RefMonster_AssignedItemDrop")
            self.progress.emit("Cleared current data...")

            # Restore from backup
            cursor.execute("""
                INSERT INTO _RefMonster_AssignedItemDrop
                SELECT * FROM _RefMonster_AssignedItemDrop_Backup
            """)

            # Count rows
            cursor.execute("SELECT COUNT(*) FROM _RefMonster_AssignedItemDrop")
            count = cursor.fetchone()[0]

            conn.commit()
            conn.close()

            self.finished.emit(
                True, f"Restore completed successfully!\n{count} rows restored."
            )

        except Exception as e:
            self.finished.emit(False, f"Restore failed: {str(e)}")


class DropRateWorker(QThread):
    """Worker thread to handle database operations without blocking UI."""

    progress = pyqtSignal(str)
    progress_percent = pyqtSignal(int, str)  # percentage, ETA
    finished = pyqtSignal(bool, str)

    def __init__(self, db_config, rare_types, probabilities, level_distance):
        super().__init__()
        self.db_config = db_config
        self.rare_types = rare_types  # List of ('A', 'B', 'C') for enabled types
        self.probabilities = probabilities  # Dict: {'A': 0.01, 'B': 0.02, 'C': 0.03}
        self.level_distance = level_distance

    def run(self):
        """Execute the drop rate update with optimized batch operations."""
        try:
            start_time = time.time()
            conn = mssql_python.connect(self.db_config)
            cursor = conn.cursor()

            # Step 1: Collect all items and their monster assignments
            self.progress.emit("Step 1/4: Collecting items...")
            self.progress_percent.emit(10, "Analyzing...")

            all_assignments = []  # (monster_id, item_id, drop_ratio)
            item_ids_to_delete = set()
            total_monsters = 0
            item_count = 0

            for rare_type in self.rare_types:
                cursor.execute(
                    """
                    SELECT ID, CodeName128, ReqLevel1
                    FROM _RefObjCommon
                    WHERE CodeName128 LIKE ?
                    AND TypeID1 = 3
                    AND ReqLevel1 IS NOT NULL
                """,
                    (f"%_{rare_type}_RARE%",),
                )
                items = cursor.fetchall()

                drop_ratio = self.probabilities[rare_type]

                for item_id, item_name, item_level in items:
                    item_count += 1
                    item_ids_to_delete.add(item_id)

                    min_level = max(1, item_level - self.level_distance)
                    max_level = item_level + self.level_distance

                    # Find monsters within level range
                    cursor.execute(
                        """
                        SELECT c.ID
                        FROM _RefObjCommon c
                        JOIN _RefObjChar ch ON c.ID = ch.ID
                        WHERE c.TypeID1 = 1 AND c.TypeID2 = 2
                        AND ch.Lvl BETWEEN ? AND ?
                    """,
                        (min_level, max_level),
                    )

                    monsters = cursor.fetchall()
                    monster_count = len(monsters)
                    total_monsters = max(total_monsters, monster_count)

                    for (monster_id,) in monsters:
                        all_assignments.append((monster_id, item_id, drop_ratio))

                    if item_count % 10 == 0:
                        self.progress.emit(
                            f"Analyzed {item_count} items, {len(all_assignments)} assignments planned"
                        )

            total_assignments = len(all_assignments)
            self.progress.emit(
                f"Step 1 complete: {item_count} items, {total_assignments} assignments to create"
            )
            self.progress_percent.emit(25, "Deleting old entries...")

            # Step 2: Delete existing entries in batch
            if item_ids_to_delete:
                self.progress.emit(
                    f"Step 2/4: Deleting old entries for {len(item_ids_to_delete)} items..."
                )

                # Delete in batches to avoid query size limits
                item_id_list = list(item_ids_to_delete)
                batch_size = 1000
                for i in range(0, len(item_id_list), batch_size):
                    batch = item_id_list[i : i + batch_size]
                    placeholders = ",".join(["?" for _ in batch])
                    cursor.execute(
                        f"""
                        DELETE FROM _RefMonster_AssignedItemDrop
                        WHERE RefItemID IN ({placeholders})
                    """,
                        batch,
                    )
                    self.progress.emit(
                        f"Deleted entries for {min(i + batch_size, len(item_id_list))}/{len(item_id_list)} items"
                    )

            self.progress_percent.emit(40, "Inserting new entries...")
            self.progress.emit("Step 3/4: Inserting new drop entries...")

            # Step 3: Insert new entries in large batches
            batch_size = 500  # SQL Server can handle up to 1000 rows per INSERT, use 500 for safety
            inserted = 0

            for i in range(0, total_assignments, batch_size):
                batch = all_assignments[i : i + batch_size]

                # Build batch INSERT statement
                values_parts = []
                params = []
                for monster_id, item_id, drop_ratio in batch:
                    values_parts.append(
                        "(?, ?, 0, 0, 1, 1, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 'xxx')"
                    )
                    params.extend([monster_id, item_id, drop_ratio])

                values_clause = ",".join(values_parts)

                insert_sql = f"""
                    INSERT INTO _RefMonster_AssignedItemDrop
                    (RefMonsterID, RefItemID, DropGroupType, OptLevel,
                     DropAmountMin, DropAmountMax, DropRatio,
                     RefMagicOptionID1, CustomValue1, RefMagicOptionID2, CustomValue2,
                     RefMagicOptionID3, CustomValue3, RefMagicOptionID4, CustomValue4,
                     RefMagicOptionID5, CustomValue5, RefMagicOptionID6, CustomValue6,
                     RefMagicOptionID7, CustomValue7, RefMagicOptionID8, CustomValue8,
                     RefMagicOptionID9, CustomValue9, RentCodeName)
                    VALUES {values_clause}
                """

                cursor.execute(insert_sql, params)
                inserted += len(batch)

                # Update progress
                percentage = 40 + int((inserted / total_assignments) * 50)
                elapsed_time = time.time() - start_time
                if inserted > 0:
                    time_per_insert = elapsed_time / inserted
                    remaining = total_assignments - inserted
                    eta_seconds = int(time_per_insert * remaining)

                    if eta_seconds < 60:
                        eta_str = f"{eta_seconds}s"
                    elif eta_seconds < 3600:
                        eta_str = f"{eta_seconds // 60}m {eta_seconds % 60}s"
                    else:
                        hours = eta_seconds // 3600
                        minutes = (eta_seconds % 3600) // 60
                        eta_str = f"{hours}h {minutes}m"
                else:
                    eta_str = "Calculating..."

                self.progress_percent.emit(percentage, f"ETA: {eta_str}")
                self.progress.emit(
                    f"Inserted {inserted}/{total_assignments} assignments ({percentage - 40}% of inserts)"
                )

            self.progress_percent.emit(90, "Committing...")
            self.progress.emit("Step 4/4: Committing changes to database...")

            conn.commit()
            conn.close()

            self.progress_percent.emit(100, "Complete!")

            elapsed = time.time() - start_time
            elapsed_str = (
                f"{int(elapsed // 60)}m {int(elapsed % 60)}s"
                if elapsed >= 60
                else f"{int(elapsed)}s"
            )

            summary = (
                f"Successfully updated drop rates in {elapsed_str}!\n\n"
                f"Items processed: {item_count}\n"
                f"Monsters affected: {total_monsters}\n"
                f"Total assignments: {total_assignments}"
            )

            self.finished.emit(True, summary)

        except Exception as e:
            self.finished.emit(False, f"Error: {str(e)}")


class RareDropTool(QMainWindow):
    CONFIG_FILE = "db_config.json"

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Rare Item Drop Probability Tool")
        self.setGeometry(100, 100, 500, 400)

        # Load database connection parameters from config file
        self.load_config()

        # Create central widget and layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # Title
        title = QLabel("Rare Item Drop Probability Configuration")
        title.setStyleSheet("font-size: 14pt; font-weight: bold; padding: 10px;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # Form layout for inputs
        form_layout = QFormLayout()

        # Star (A_RARE)
        star_layout = QHBoxLayout()
        self.star_checkbox = QCheckBox("Enable")
        self.star_checkbox.setChecked(True)
        self.star_prob_input = QLineEdit("0.01")
        self.star_prob_input.setPlaceholderText("0.01 for 1%")
        self.star_prob_input.setFixedWidth(150)
        star_layout.addWidget(self.star_checkbox)
        star_layout.addWidget(self.star_prob_input)
        star_layout.addStretch()
        form_layout.addRow("Seal of Star:", star_layout)

        # Moon (B_RARE)
        moon_layout = QHBoxLayout()
        self.moon_checkbox = QCheckBox("Enable")
        self.moon_checkbox.setChecked(True)
        self.moon_prob_input = QLineEdit("0.005")
        self.moon_prob_input.setPlaceholderText("0.01 for 1%")
        self.moon_prob_input.setFixedWidth(150)
        moon_layout.addWidget(self.moon_checkbox)
        moon_layout.addWidget(self.moon_prob_input)
        moon_layout.addStretch()
        form_layout.addRow("Seal of Moon:", moon_layout)

        # Sun (C_RARE)
        sun_layout = QHBoxLayout()
        self.sun_checkbox = QCheckBox("Enable")
        self.sun_checkbox.setChecked(True)
        self.sun_prob_input = QLineEdit("0.001")
        self.sun_prob_input.setPlaceholderText("0.01 for 1%")
        self.sun_prob_input.setFixedWidth(150)
        sun_layout.addWidget(self.sun_checkbox)
        sun_layout.addWidget(self.sun_prob_input)
        sun_layout.addStretch()
        form_layout.addRow("Seal of Sun:", sun_layout)

        # Level distance
        self.level_distance_input = QLineEdit("10")
        self.level_distance_input.setPlaceholderText("e.g., 10")
        self.level_distance_input.setFixedWidth(150)
        form_layout.addRow("Level Distance (±):", self.level_distance_input)

        layout.addLayout(form_layout)

        # Info label
        info_label = QLabel(
            "Items will be assigned to monsters within ± the level distance.\n"
            "Example: A level 100 item with distance 10 drops from level 90-110 monsters."
        )
        info_label.setStyleSheet(
            "padding: 10px; background-color: #e7f3ff; border-radius: 5px;"
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        # Progress bar with ETA (hidden by default)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setMaximum(100)
        layout.addWidget(self.progress_bar)

        # ETA label (hidden by default)
        self.eta_label = QLabel("")
        self.eta_label.setVisible(False)
        self.eta_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.eta_label.setStyleSheet("padding: 5px; font-weight: bold;")
        layout.addWidget(self.eta_label)

        # Button layout - main actions
        button_layout = QHBoxLayout()

        # Apply button
        self.apply_button = QPushButton("Apply Drop Rates")
        self.apply_button.clicked.connect(self.apply_drop_rates)
        self.apply_button.setStyleSheet("padding: 10px; font-size: 12pt;")
        button_layout.addWidget(self.apply_button)

        # Test connection button
        self.test_button = QPushButton("Test Connection")
        self.test_button.clicked.connect(self.test_connection)
        button_layout.addWidget(self.test_button)

        # Settings button
        self.settings_button = QPushButton("Database Settings")
        self.settings_button.clicked.connect(self.show_settings)
        button_layout.addWidget(self.settings_button)

        layout.addLayout(button_layout)

        # Backup/Restore button layout
        backup_layout = QHBoxLayout()

        # Backup button
        self.backup_button = QPushButton("Create Backup")
        self.backup_button.clicked.connect(self.create_backup)
        self.backup_button.setStyleSheet("padding: 8px;")
        backup_layout.addWidget(self.backup_button)

        # Restore button
        self.restore_button = QPushButton("Restore from Backup")
        self.restore_button.clicked.connect(self.restore_backup)
        self.restore_button.setStyleSheet("padding: 8px;")
        backup_layout.addWidget(self.restore_button)

        layout.addLayout(backup_layout)

        # Status label
        self.status_label = QLabel("Ready")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("padding: 10px; background-color: #f0f0f0;")
        layout.addWidget(self.status_label)

        # Add stretch to push everything to top
        layout.addStretch()

        # Worker thread
        self.worker = None

        # Check if backup exists, create if not
        self.check_and_create_initial_backup()

    def load_config(self):
        """Load database configuration from file or use defaults."""
        default_config = {
            "server": "localhost",
            "port": 1433,
            "database": "SRO_VT_SHARD",
            "user": "sa",
            "password": "",
        }

        if os.path.exists(self.CONFIG_FILE):
            try:
                with open(self.CONFIG_FILE, "r") as f:
                    config = json.load(f)
                self.server = config.get("server", default_config["server"])
                self.port = config.get("port", default_config["port"])
                self.database = config.get("database", default_config["database"])
                self.user = config.get("user", default_config["user"])
                self.password = config.get("password", default_config["password"])
            except Exception:
                # If config file is corrupted, use defaults
                self.server = default_config["server"]
                self.port = default_config["port"]
                self.database = default_config["database"]
                self.user = default_config["user"]
                self.password = default_config["password"]
        else:
            # Use defaults
            self.server = default_config["server"]
            self.port = default_config["port"]
            self.database = default_config["database"]
            self.user = default_config["user"]
            self.password = default_config["password"]

    def save_config(self):
        """Save database configuration to file."""
        config = {
            "server": self.server,
            "port": self.port,
            "database": self.database,
            "user": self.user,
            "password": self.password,
        }
        try:
            with open(self.CONFIG_FILE, "w") as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            QMessageBox.warning(
                self, "Config Save Failed", f"Could not save configuration: {str(e)}"
            )

    def show_settings(self):
        """Show database settings dialog."""
        current_settings = {
            "server": self.server,
            "port": self.port,
            "database": self.database,
            "user": self.user,
            "password": self.password,
        }

        dialog = DatabaseSettingsDialog(self, current_settings)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_settings = dialog.get_settings()
            self.server = new_settings["server"]
            self.port = new_settings["port"]
            self.database = new_settings["database"]
            self.user = new_settings["user"]
            self.password = new_settings["password"]
            self.save_config()

            QMessageBox.information(
                self,
                "Settings Saved",
                "Database connection settings have been saved.\n\n"
                "Click 'Test Connection' to verify the new settings.",
            )

    def get_connection_string(self):
        """Get the database connection string."""
        return (
            f"SERVER={self.server},{self.port};"
            f"DATABASE={self.database};"
            f"UID={self.user};"
            f"PWD={self.password};"
            f"Encrypt=yes;"
            f"TrustServerCertificate=yes;"
        )

    def test_connection(self):
        """Test the database connection."""
        try:
            conn = mssql_python.connect(self.get_connection_string())
            cursor = conn.cursor()
            cursor.execute("SELECT @@VERSION")
            version = cursor.fetchone()
            conn.close()

            QMessageBox.information(
                self,
                "Connection Success",
                f"Successfully connected to database!\n\nServer version:\n{version[0][:100]}...",
            )
            self.status_label.setText("Connection successful")
            self.status_label.setStyleSheet(
                "padding: 10px; background-color: #d4edda; color: #155724;"
            )
        except Exception as e:
            QMessageBox.critical(
                self, "Connection Failed", f"Failed to connect to database:\n{str(e)}"
            )
            self.status_label.setText(f"Connection failed: {str(e)}")
            self.status_label.setStyleSheet(
                "padding: 10px; background-color: #f8d7da; color: #721c24;"
            )

    def check_and_create_initial_backup(self):
        """Check if backup exists, create if this is the first run."""
        try:
            conn = mssql_python.connect(self.get_connection_string())
            cursor = conn.cursor()

            # Check if backup table exists
            cursor.execute("""
                SELECT COUNT(*)
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_NAME = '_RefMonster_AssignedItemDrop_Backup'
            """)
            backup_exists = cursor.fetchone()[0] > 0
            conn.close()

            if not backup_exists:
                reply = QMessageBox.question(
                    self,
                    "First Run - Create Backup?",
                    "No backup detected. This appears to be the first time running this tool.\n\n"
                    "Would you like to create an initial backup of your drop table?\n"
                    "This is highly recommended to restore original data if needed.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )

                if reply == QMessageBox.StandardButton.Yes:
                    self.create_backup()
                else:
                    self.status_label.setText("Warning: No backup exists")
                    self.status_label.setStyleSheet(
                        "padding: 10px; background-color: #fff3cd; color: #856404;"
                    )
            else:
                self.status_label.setText("Backup exists - Ready to use")
                self.status_label.setStyleSheet(
                    "padding: 10px; background-color: #d4edda; color: #155724;"
                )

        except Exception as e:
            # Don't fail startup if we can't check backup
            self.status_label.setText(f"Could not check backup: {str(e)[:50]}")
            self.status_label.setStyleSheet(
                "padding: 10px; background-color: #fff3cd; color: #856404;"
            )

    def create_backup(self):
        """Create a backup of the drop table."""
        reply = QMessageBox.question(
            self,
            "Confirm Backup",
            "This will overwrite any existing backup.\n\n"
            "Current drop table will be backed up to:\n"
            "_RefMonster_AssignedItemDrop_Backup\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )

        if reply == QMessageBox.StandardButton.No:
            return

        # Disable controls
        self.apply_button.setEnabled(False)
        self.test_button.setEnabled(False)
        self.backup_button.setEnabled(False)
        self.restore_button.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(0)  # Indeterminate
        self.status_label.setText("Creating backup...")
        self.status_label.setStyleSheet(
            "padding: 10px; background-color: #fff3cd; color: #856404;"
        )

        # Start worker
        self.worker = BackupWorker(self.get_connection_string())
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_backup_finished)
        self.worker.start()

    def restore_backup(self):
        """Restore the drop table from backup."""
        reply = QMessageBox.warning(
            self,
            "Confirm Restore",
            "WARNING: This will DELETE all current drop entries and restore from backup!\n\n"
            "This cannot be undone. Are you sure?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.No:
            return

        # Disable controls
        self.apply_button.setEnabled(False)
        self.test_button.setEnabled(False)
        self.backup_button.setEnabled(False)
        self.restore_button.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(0)  # Indeterminate
        self.status_label.setText("Restoring from backup...")
        self.status_label.setStyleSheet(
            "padding: 10px; background-color: #fff3cd; color: #856404;"
        )

        # Start worker
        self.worker = RestoreWorker(self.get_connection_string())
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_restore_finished)
        self.worker.start()

    def on_backup_finished(self, success, message):
        """Handle backup completion."""
        # Re-enable controls
        self.apply_button.setEnabled(True)
        self.test_button.setEnabled(True)
        self.backup_button.setEnabled(True)
        self.restore_button.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.progress_bar.setMaximum(100)

        if success:
            QMessageBox.information(self, "Backup Complete", message)
            self.status_label.setText("Backup created successfully")
            self.status_label.setStyleSheet(
                "padding: 10px; background-color: #d4edda; color: #155724;"
            )
        else:
            QMessageBox.critical(self, "Backup Failed", message)
            self.status_label.setText("Backup failed")
            self.status_label.setStyleSheet(
                "padding: 10px; background-color: #f8d7da; color: #721c24;"
            )

    def on_restore_finished(self, success, message):
        """Handle restore completion."""
        # Re-enable controls
        self.apply_button.setEnabled(True)
        self.test_button.setEnabled(True)
        self.backup_button.setEnabled(True)
        self.restore_button.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.progress_bar.setMaximum(100)

        if success:
            QMessageBox.information(self, "Restore Complete", message)
            self.status_label.setText("Restore completed successfully")
            self.status_label.setStyleSheet(
                "padding: 10px; background-color: #d4edda; color: #155724;"
            )
        else:
            QMessageBox.critical(self, "Restore Failed", message)
            self.status_label.setText("Restore failed")
            self.status_label.setStyleSheet(
                "padding: 10px; background-color: #f8d7da; color: #721c24;"
            )

    def apply_drop_rates(self):
        """Apply the drop rate configuration."""
        # Validate inputs
        try:
            rare_types = []
            probabilities = {}

            if self.star_checkbox.isChecked():
                prob = float(self.star_prob_input.text().strip())
                if prob <= 0 or prob > 1:
                    raise ValueError("Star probability must be between 0 and 1")
                rare_types.append("A")
                probabilities["A"] = prob

            if self.moon_checkbox.isChecked():
                prob = float(self.moon_prob_input.text().strip())
                if prob <= 0 or prob > 1:
                    raise ValueError("Moon probability must be between 0 and 1")
                rare_types.append("B")
                probabilities["B"] = prob

            if self.sun_checkbox.isChecked():
                prob = float(self.sun_prob_input.text().strip())
                if prob <= 0 or prob > 1:
                    raise ValueError("Sun probability must be between 0 and 1")
                rare_types.append("C")
                probabilities["C"] = prob

            if not rare_types:
                QMessageBox.warning(
                    self,
                    "No Types Selected",
                    "Please select at least one rare item type.",
                )
                return

            level_distance = int(self.level_distance_input.text().strip())
            if level_distance < 0:
                raise ValueError("Level distance must be non-negative")

        except ValueError as e:
            QMessageBox.warning(
                self, "Invalid Input", f"Please check your inputs:\n{str(e)}"
            )
            return

        # Confirm action
        types_str = ", ".join([f"{t}_RARE" for t in rare_types])
        reply = QMessageBox.question(
            self,
            "Confirm Action",
            f"This will update drop rates for:\n{types_str}\n\n"
            f"Level distance: ±{level_distance}\n\n"
            f"This operation will DELETE existing drop entries for these items.\n"
            f"Do you want to continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.No:
            return

        # Disable controls during operation
        self.apply_button.setEnabled(False)
        self.test_button.setEnabled(False)
        self.backup_button.setEnabled(False)
        self.restore_button.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.eta_label.setVisible(True)
        self.eta_label.setText("Starting...")
        self.status_label.setText("Processing...")
        self.status_label.setStyleSheet(
            "padding: 10px; background-color: #fff3cd; color: #856404;"
        )

        # Start worker thread
        self.worker = DropRateWorker(
            self.get_connection_string(), rare_types, probabilities, level_distance
        )
        self.worker.progress.connect(self.on_progress)
        self.worker.progress_percent.connect(self.on_progress_percent)
        self.worker.finished.connect(self.on_finished)
        self.worker.start()

    def on_progress(self, message):
        """Handle progress updates from worker."""
        self.status_label.setText(message)

    def on_progress_percent(self, percentage, eta):
        """Handle progress percentage updates."""
        self.progress_bar.setValue(percentage)
        self.eta_label.setText(eta)

    def on_finished(self, success, message):
        """Handle completion from worker."""
        # Re-enable controls
        self.apply_button.setEnabled(True)
        self.test_button.setEnabled(True)
        self.backup_button.setEnabled(True)
        self.restore_button.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.eta_label.setVisible(False)

        if success:
            QMessageBox.information(self, "Success", message)
            self.status_label.setText("Operation completed successfully")
            self.status_label.setStyleSheet(
                "padding: 10px; background-color: #d4edda; color: #155724;"
            )
        else:
            QMessageBox.critical(self, "Error", message)
            self.status_label.setText("Operation failed")
            self.status_label.setStyleSheet(
                "padding: 10px; background-color: #f8d7da; color: #721c24;"
            )


def main():
    app = QApplication(sys.argv)
    window = RareDropTool()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
