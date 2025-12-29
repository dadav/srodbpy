import sys
import mssql_python
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QCheckBox, QMessageBox, QFormLayout,
    QProgressBar
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal


class DropRateWorker(QThread):
    """Worker thread to handle database operations without blocking UI."""
    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, db_config, rare_types, probabilities, level_distance):
        super().__init__()
        self.db_config = db_config
        self.rare_types = rare_types  # List of ('A', 'B', 'C') for enabled types
        self.probabilities = probabilities  # Dict: {'A': 0.01, 'B': 0.02, 'C': 0.03}
        self.level_distance = level_distance

    def run(self):
        """Execute the drop rate update."""
        try:
            conn = mssql_python.connect(self.db_config)
            cursor = conn.cursor()

            total_items = 0
            total_monsters = 0
            total_assignments = 0

            for rare_type in self.rare_types:
                self.progress.emit(f"Processing {rare_type}_RARE items...")

                # Get all items of this rare type
                cursor.execute("""
                    SELECT ID, CodeName128, ReqLevel1
                    FROM _RefObjCommon
                    WHERE CodeName128 LIKE ?
                    AND TypeID1 = 3
                    AND ReqLevel1 IS NOT NULL
                """, (f'%_{rare_type}_RARE%',))

                items = cursor.fetchall()
                total_items += len(items)
                self.progress.emit(f"Found {len(items)} {rare_type}_RARE items")

                drop_ratio = self.probabilities[rare_type]

                for item_id, item_name, item_level in items:
                    min_level = max(1, item_level - self.level_distance)
                    max_level = item_level + self.level_distance

                    # Find monsters within level range
                    cursor.execute("""
                        SELECT c.ID
                        FROM _RefObjCommon c
                        JOIN _RefObjChar ch ON c.ID = ch.ID
                        WHERE c.TypeID1 = 1 AND c.TypeID2 = 2
                        AND ch.Lvl BETWEEN ? AND ?
                    """, (min_level, max_level))

                    monsters = cursor.fetchall()

                    if not monsters:
                        continue

                    monster_count = len(monsters)
                    total_monsters = max(total_monsters, monster_count)

                    # Delete existing drop entries for this item
                    cursor.execute("""
                        DELETE FROM _RefMonster_AssignedItemDrop
                        WHERE RefItemID = ?
                    """, (item_id,))

                    # Insert new drop entries
                    for (monster_id,) in monsters:
                        try:
                            cursor.execute("""
                                INSERT INTO _RefMonster_AssignedItemDrop
                                (RefMonsterID, RefItemID, DropGroupType, OptLevel,
                                 DropAmountMin, DropAmountMax, DropRatio,
                                 RefMagicOptionID1, CustomValue1, RefMagicOptionID2, CustomValue2,
                                 RefMagicOptionID3, CustomValue3, RefMagicOptionID4, CustomValue4,
                                 RefMagicOptionID5, CustomValue5, RefMagicOptionID6, CustomValue6,
                                 RefMagicOptionID7, CustomValue7, RefMagicOptionID8, CustomValue8,
                                 RefMagicOptionID9, CustomValue9, RentCodeName)
                                VALUES (?, ?, 0, 0, 1, 1, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 'xxx')
                            """, (monster_id, item_id, drop_ratio))
                            total_assignments += 1
                        except Exception as e:
                            # Skip duplicates or constraint violations
                            if "duplicate" not in str(e).lower() and "constraint" not in str(e).lower():
                                self.progress.emit(f"Warning: {str(e)[:100]}")

                    self.progress.emit(f"Assigned {item_name} (Lvl {item_level}) to {monster_count} monsters")

            conn.commit()
            conn.close()

            summary = (f"Successfully updated drop rates!\n\n"
                      f"Items processed: {total_items}\n"
                      f"Monsters affected: {total_monsters}\n"
                      f"Total assignments: {total_assignments}")

            self.finished.emit(True, summary)

        except Exception as e:
            self.finished.emit(False, f"Error: {str(e)}")


class RareDropTool(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Rare Item Drop Probability Tool")
        self.setGeometry(100, 100, 500, 400)

        # Database connection parameters
        self.server = "localhost"
        self.port = 1433
        self.database = "SRO_VT_SHARD"
        self.user = "sa"
        self.password = "Foobarfoobar2"

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
        info_label.setStyleSheet("padding: 10px; background-color: #e7f3ff; border-radius: 5px;")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        # Progress bar (hidden by default)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setMaximum(0)  # Indeterminate progress
        layout.addWidget(self.progress_bar)

        # Button layout
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

        layout.addLayout(button_layout)

        # Status label
        self.status_label = QLabel("Ready")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("padding: 10px; background-color: #f0f0f0;")
        layout.addWidget(self.status_label)

        # Add stretch to push everything to top
        layout.addStretch()

        # Worker thread
        self.worker = None

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
                f"Successfully connected to database!\n\nServer version:\n{version[0][:100]}..."
            )
            self.status_label.setText("Connection successful")
            self.status_label.setStyleSheet("padding: 10px; background-color: #d4edda; color: #155724;")
        except Exception as e:
            QMessageBox.critical(
                self,
                "Connection Failed",
                f"Failed to connect to database:\n{str(e)}"
            )
            self.status_label.setText(f"Connection failed: {str(e)}")
            self.status_label.setStyleSheet("padding: 10px; background-color: #f8d7da; color: #721c24;")

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
                rare_types.append('A')
                probabilities['A'] = prob

            if self.moon_checkbox.isChecked():
                prob = float(self.moon_prob_input.text().strip())
                if prob <= 0 or prob > 1:
                    raise ValueError("Moon probability must be between 0 and 1")
                rare_types.append('B')
                probabilities['B'] = prob

            if self.sun_checkbox.isChecked():
                prob = float(self.sun_prob_input.text().strip())
                if prob <= 0 or prob > 1:
                    raise ValueError("Sun probability must be between 0 and 1")
                rare_types.append('C')
                probabilities['C'] = prob

            if not rare_types:
                QMessageBox.warning(
                    self,
                    "No Types Selected",
                    "Please select at least one rare item type."
                )
                return

            level_distance = int(self.level_distance_input.text().strip())
            if level_distance < 0:
                raise ValueError("Level distance must be non-negative")

        except ValueError as e:
            QMessageBox.warning(
                self,
                "Invalid Input",
                f"Please check your inputs:\n{str(e)}"
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
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.No:
            return

        # Disable controls during operation
        self.apply_button.setEnabled(False)
        self.test_button.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.status_label.setText("Processing...")
        self.status_label.setStyleSheet("padding: 10px; background-color: #fff3cd; color: #856404;")

        # Start worker thread
        self.worker = DropRateWorker(
            self.get_connection_string(),
            rare_types,
            probabilities,
            level_distance
        )
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_finished)
        self.worker.start()

    def on_progress(self, message):
        """Handle progress updates from worker."""
        self.status_label.setText(message)

    def on_finished(self, success, message):
        """Handle completion from worker."""
        # Re-enable controls
        self.apply_button.setEnabled(True)
        self.test_button.setEnabled(True)
        self.progress_bar.setVisible(False)

        if success:
            QMessageBox.information(
                self,
                "Success",
                message
            )
            self.status_label.setText("Operation completed successfully")
            self.status_label.setStyleSheet("padding: 10px; background-color: #d4edda; color: #155724;")
        else:
            QMessageBox.critical(
                self,
                "Error",
                message
            )
            self.status_label.setText("Operation failed")
            self.status_label.setStyleSheet("padding: 10px; background-color: #f8d7da; color: #721c24;")


def main():
    app = QApplication(sys.argv)
    window = RareDropTool()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
