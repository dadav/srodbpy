import sys
import time
import json
import os
from importlib.metadata import version, PackageNotFoundError
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
    QTextEdit,
    QScrollArea,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal


def get_version():
    """Read version from package metadata or pyproject.toml"""
    # Try importlib.metadata first (works for installed packages and PyInstaller)
    try:
        return version("srodbpy")
    except PackageNotFoundError:
        pass

    # Fallback: read from pyproject.toml (for development)
    try:
        import tomllib

        pyproject_path = os.path.join(os.path.dirname(__file__), "pyproject.toml")
        with open(pyproject_path, "rb") as f:
            pyproject = tomllib.load(f)
        return pyproject["project"]["version"]
    except Exception as e:
        raise RuntimeError(
            "Could not determine version. Please ensure:\n"
            "1. Package is installed with 'uv pip install -e .', or\n"
            "2. pyproject.toml is accessible in the application directory.\n"
            f"Error: {e}"
        )


__VERSION__ = get_version()


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
        """Create backup of drop tables."""
        try:
            conn = mssql_python.connect(self.db_config)
            cursor = conn.cursor()

            self.progress.emit("Creating backup of drop groups...")

            # Backup _RefDropItemGroup
            cursor.execute("""
                IF OBJECT_ID('_RefDropItemGroup_Backup', 'U') IS NOT NULL
                    DROP TABLE _RefDropItemGroup_Backup
            """)

            cursor.execute("""
                SELECT *
                INTO _RefDropItemGroup_Backup
                FROM _RefDropItemGroup
            """)

            cursor.execute("SELECT COUNT(*) FROM _RefDropItemGroup_Backup")
            group_count = cursor.fetchone()[0]

            self.progress.emit("Creating backup of drop assignments...")

            # Backup _RefMonster_AssignedItemRndDrop
            cursor.execute("""
                IF OBJECT_ID('_RefMonster_AssignedItemRndDrop_Backup', 'U') IS NOT NULL
                    DROP TABLE _RefMonster_AssignedItemRndDrop_Backup
            """)

            cursor.execute("""
                SELECT *
                INTO _RefMonster_AssignedItemRndDrop_Backup
                FROM _RefMonster_AssignedItemRndDrop
            """)

            cursor.execute(
                "SELECT COUNT(*) FROM _RefMonster_AssignedItemRndDrop_Backup"
            )
            assignment_count = cursor.fetchone()[0]

            conn.commit()
            conn.close()

            self.finished.emit(
                True,
                f"Backup created successfully!\n\n"
                f"Drop groups backed up: {group_count}\n"
                f"Assignments backed up: {assignment_count}",
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
        """Restore drop tables from backup."""
        try:
            conn = mssql_python.connect(self.db_config)
            cursor = conn.cursor()

            self.progress.emit("Checking for backups...")

            # Check if both backups exist
            cursor.execute("""
                SELECT COUNT(*)
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_NAME = '_RefDropItemGroup_Backup'
            """)
            if cursor.fetchone()[0] == 0:
                raise Exception(
                    "No drop group backup found! Please create a backup first."
                )

            cursor.execute("""
                SELECT COUNT(*)
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_NAME = '_RefMonster_AssignedItemRndDrop_Backup'
            """)
            if cursor.fetchone()[0] == 0:
                raise Exception(
                    "No assignment backup found! Please create a backup first."
                )

            # Check if backups have any data
            cursor.execute("SELECT COUNT(*) FROM _RefDropItemGroup_Backup")
            backup_group_count = cursor.fetchone()[0]

            cursor.execute(
                "SELECT COUNT(*) FROM _RefMonster_AssignedItemRndDrop_Backup"
            )
            backup_assignment_count = cursor.fetchone()[0]

            if backup_group_count == 0 and backup_assignment_count == 0:
                raise Exception(
                    "Backup tables are empty!\n\n"
                    "The backup contains no data. This should not happen.\n"
                    "Please create a new backup before attempting to restore."
                )

            self.progress.emit("Clearing current data...")

            # Clear all current rows from both tables
            cursor.execute("DELETE FROM _RefMonster_AssignedItemRndDrop")
            cursor.execute("DELETE FROM _RefDropItemGroup")

            self.progress.emit("Restoring drop groups from backup...")

            # Restore _RefDropItemGroup from backup
            cursor.execute("""
                INSERT INTO _RefDropItemGroup
                SELECT * FROM _RefDropItemGroup_Backup
            """)

            cursor.execute("SELECT COUNT(*) FROM _RefDropItemGroup")
            group_count = cursor.fetchone()[0]

            self.progress.emit("Restoring drop assignments from backup...")

            # Restore _RefMonster_AssignedItemRndDrop from backup
            cursor.execute("""
                INSERT INTO _RefMonster_AssignedItemRndDrop
                SELECT * FROM _RefMonster_AssignedItemRndDrop_Backup
            """)

            cursor.execute("SELECT COUNT(*) FROM _RefMonster_AssignedItemRndDrop")
            assignment_count = cursor.fetchone()[0]

            conn.commit()
            conn.close()

            self.finished.emit(
                True,
                f"Restore completed successfully!\n\n"
                f"Drop groups restored: {group_count}\n"
                f"Assignments restored: {assignment_count}",
            )

        except Exception as e:
            self.finished.emit(False, f"Restore failed: {str(e)}")


class DropRateWorker(QThread):
    """Worker thread to handle database operations without blocking UI."""

    progress = pyqtSignal(str)
    progress_percent = pyqtSignal(int, str)  # percentage, ETA
    finished = pyqtSignal(bool, str)

    def __init__(self, db_config, rare_types, probabilities, level_distance, country_mixture, level_threshold, decrease_pct):
        super().__init__()
        self.db_config = db_config
        self.rare_types = rare_types  # List of ('A', 'B', 'C') for enabled types
        self.probabilities = probabilities  # Dict: {'A': 0.01, 'B': 0.02, 'C': 0.03}
        self.level_distance = level_distance
        self.country_mixture = country_mixture  # Bool: True = allow cross-region, False = same region only
        self.level_threshold = level_threshold  # Monster level threshold - monsters at or below get full probability
        self.decrease_pct = decrease_pct  # Percentage decrease per level above threshold

    @staticmethod
    def get_region(country):
        """Map country code to region. Country 0 and 3 = Chinese, Country 1 = Europe."""
        if country in (0, 3):
            return "CN"  # Chinese
        elif country == 1:
            return "EU"  # Europe
        else:
            return f"R{country}"  # Other regions use R prefix

    def run(self):
        """Execute the drop rate update using group-based approach."""
        try:
            start_time = time.time()
            conn = mssql_python.connect(self.db_config)
            cursor = conn.cursor()

            # Step 0: Create backup if it doesn't exist (safety measure)
            self.progress.emit("Checking for backup...")
            self.progress_percent.emit(2, "Checking...")

            cursor.execute("""
                SELECT COUNT(*)
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_NAME IN ('_RefDropItemGroup_Backup', '_RefMonster_AssignedItemRndDrop_Backup')
            """)
            backup_exists = cursor.fetchone()[0] >= 2

            if not backup_exists:
                self.progress.emit(
                    "Creating automatic backup before applying changes..."
                )
                self.progress_percent.emit(3, "Creating backup...")

                # Create backup of drop groups (all rows to preserve original state)
                cursor.execute("""
                    IF OBJECT_ID('_RefDropItemGroup_Backup', 'U') IS NOT NULL
                        DROP TABLE _RefDropItemGroup_Backup
                """)
                cursor.execute("""
                    SELECT *
                    INTO _RefDropItemGroup_Backup
                    FROM _RefDropItemGroup
                """)

                # Create backup of assignments (all rows to preserve original state)
                cursor.execute("""
                    IF OBJECT_ID('_RefMonster_AssignedItemRndDrop_Backup', 'U') IS NOT NULL
                        DROP TABLE _RefMonster_AssignedItemRndDrop_Backup
                """)
                cursor.execute("""
                    SELECT *
                    INTO _RefMonster_AssignedItemRndDrop_Backup
                    FROM _RefMonster_AssignedItemRndDrop
                """)

                self.progress.emit("Backup created successfully")

            # Step 1: Collect items and organize by (rare_type, level) or (rare_type, level, region)
            if self.country_mixture:
                self.progress.emit("Step 1/6: Collecting items and organizing by level (region mixture enabled)...")
            else:
                self.progress.emit("Step 1/6: Collecting items and organizing by level and region...")
            self.progress_percent.emit(5, "Analyzing...")

            # Dictionary: {(rare_type, level) or (rare_type, level, region): [item_ids]}
            items_by_type_level = {}
            item_count = 0

            for rare_type in self.rare_types:
                cursor.execute(
                    """
                    SELECT ID, ReqLevel1, Country
                    FROM _RefObjCommon
                    WHERE CodeName128 LIKE ?
                    AND Service = 1
                    AND TypeID1 = 3
                    AND ReqLevel1 IS NOT NULL
                """,
                    (f"%_{rare_type}_RARE%",),
                )
                items = cursor.fetchall()

                for item_id, item_level, country in items:
                    item_count += 1
                    # Organize by region if country_mixture is disabled
                    if self.country_mixture:
                        key = (rare_type, item_level)
                    else:
                        region = self.get_region(country)
                        key = (rare_type, item_level, region)

                    if key not in items_by_type_level:
                        items_by_type_level[key] = []
                    items_by_type_level[key].append(item_id)

                    if item_count % 100 == 0:
                        self.progress.emit(f"Collected {item_count} items...")

            self.progress.emit(
                f"Step 1 complete: {item_count} items organized into {len(items_by_type_level)} groups"
            )
            self.progress_percent.emit(10, "Deleting old groups...")

            # Step 2: Delete old rare drop groups from _RefDropItemGroup
            self.progress.emit("Step 2/6: Deleting old rare drop groups...")
            cursor.execute(
                "DELETE FROM _RefDropItemGroup WHERE CodeName128 LIKE 'RARE_%'"
            )
            deleted_groups = cursor.rowcount
            self.progress.emit(f"Deleted {deleted_groups} old rare drop group entries")
            self.progress_percent.emit(15, "Creating new groups...")

            # Step 3: Create drop groups in _RefDropItemGroup
            self.progress.emit("Step 3/6: Creating drop groups in _RefDropItemGroup...")

            # Get next available group ID
            cursor.execute("SELECT MAX(RefItemGroupID) FROM _RefDropItemGroup")
            max_group_id = cursor.fetchone()[0] or 0
            next_group_id = max_group_id + 1

            # Track created groups: {key: group_id} where key is (rare_type, level) or (rare_type, level, region)
            created_groups = {}
            group_entries = []  # [(Service, RefItemGroupID, CodeName128, RefItemID, SelectRatio, RefMagicGroupID)]

            for key, item_ids in items_by_type_level.items():
                group_id = next_group_id

                # Create group name based on whether country_mixture is enabled
                if self.country_mixture:
                    rare_type, level = key
                    group_name = f"RARE_{rare_type}_LVL_{level}"
                else:
                    rare_type, level, region = key
                    group_name = f"RARE_{rare_type}_LVL_{level}_{region}"

                created_groups[key] = group_id
                next_group_id += 1

                # Calculate equal SelectRatio for all items in group
                select_ratio = 1.0 / len(item_ids)

                for item_id in item_ids:
                    group_entries.append(
                        (1, group_id, group_name, item_id, select_ratio, 0)
                    )

            # Insert groups in batches
            # SQL Server limit: 2100 parameters per query
            # 6 columns per row = 2100/6 = 350 rows max
            batch_size = 300  # Use 300 for safety margin
            inserted_items = 0
            for i in range(0, len(group_entries), batch_size):
                batch = group_entries[i : i + batch_size]

                values_parts = []
                params = []
                for (
                    service,
                    group_id,
                    group_name,
                    item_id,
                    select_ratio,
                    magic_group,
                ) in batch:
                    values_parts.append("(?, ?, ?, ?, ?, ?)")
                    params.extend(
                        [
                            service,
                            group_id,
                            group_name,
                            item_id,
                            select_ratio,
                            magic_group,
                        ]
                    )

                values_clause = ",".join(values_parts)
                insert_sql = f"""
                    INSERT INTO _RefDropItemGroup
                    (Service, RefItemGroupID, CodeName128, RefItemID, SelectRatio, RefMagicGroupID)
                    VALUES {values_clause}
                """
                cursor.execute(insert_sql, params)
                inserted_items += len(batch)

                percentage = 15 + int((inserted_items / len(group_entries)) * 20)
                self.progress_percent.emit(percentage, "Creating groups...")
                self.progress.emit(
                    f"Created {inserted_items}/{len(group_entries)} group item entries"
                )

            self.progress.emit(
                f"Step 3 complete: Created {len(created_groups)} groups with {len(group_entries)} total items"
            )
            self.progress_percent.emit(35, "Deleting old assignments...")

            # Step 4: Delete old rare assignments from _RefMonster_AssignedItemRndDrop
            self.progress.emit("Step 4/6: Deleting old rare assignments...")
            cursor.execute(
                "DELETE FROM _RefMonster_AssignedItemRndDrop WHERE ItemGroupCodeName128 LIKE 'RARE_%'"
            )
            deleted_assignments = cursor.rowcount
            self.progress.emit(
                f"Deleted {deleted_assignments} old rare assignment entries"
            )
            self.progress_percent.emit(40, "Creating assignments...")

            # Step 5: Create assignments in _RefMonster_AssignedItemRndDrop
            self.progress.emit(
                "Step 5/6: Creating assignments in _RefMonster_AssignedItemRndDrop..."
            )

            assignments = []  # [(Service, RefMonsterID, RefItemGroupID, ItemGroupCodeName128, Overlap, DropAmountMin, DropAmountMax, DropRatio, param1, param2)]

            for key, group_id in created_groups.items():
                # Extract rare_type, level, and optionally region from key
                if self.country_mixture:
                    rare_type, level = key
                    group_name = f"RARE_{rare_type}_LVL_{level}"
                    region_filter = None
                else:
                    rare_type, level, region = key
                    group_name = f"RARE_{rare_type}_LVL_{level}_{region}"
                    region_filter = region

                drop_ratio = self.probabilities[rare_type]

                min_level = max(0, level - self.level_distance)
                max_level = level + self.level_distance

                # Find monsters within level range, optionally filtered by region
                if self.country_mixture:
                    # No region filtering - all monsters in level range
                    cursor.execute(
                        """
                        SELECT c.ID, ch.Lvl
                        FROM _RefObjCommon c
                        JOIN _RefObjChar ch ON c.Link = ch.ID
                        WHERE c.CodeName128 LIKE 'MOB_%'
                        AND c.TypeID1 = 1
                        AND c.Service = 1
                        AND ch.Lvl BETWEEN ? AND ?
                    """,
                        (min_level, max_level),
                    )
                else:
                    # Filter by region - match monsters from same region
                    if region_filter == "CN":
                        # Chinese region: country 0 or 3
                        cursor.execute(
                            """
                            SELECT c.ID, ch.Lvl
                            FROM _RefObjCommon c
                            JOIN _RefObjChar ch ON c.Link = ch.ID
                            WHERE c.CodeName128 LIKE 'MOB_%'
                            AND c.TypeID1 = 1
                            AND c.Service = 1
                            AND c.Country IN (0, 3)
                            AND ch.Lvl BETWEEN ? AND ?
                        """,
                            (min_level, max_level),
                        )
                    elif region_filter == "EU":
                        # European region: country 1
                        cursor.execute(
                            """
                            SELECT c.ID, ch.Lvl
                            FROM _RefObjCommon c
                            JOIN _RefObjChar ch ON c.Link = ch.ID
                            WHERE c.CodeName128 LIKE 'MOB_%'
                            AND c.TypeID1 = 1
                            AND c.Service = 1
                            AND c.Country = 1
                            AND ch.Lvl BETWEEN ? AND ?
                        """,
                            (min_level, max_level),
                        )
                    else:
                        # Other regions: extract country code from region name (e.g., R2 -> 2)
                        country_code = int(region_filter[1:])
                        cursor.execute(
                            """
                            SELECT c.ID, ch.Lvl
                            FROM _RefObjCommon c
                            JOIN _RefObjChar ch ON c.Link = ch.ID
                            WHERE c.CodeName128 LIKE 'MOB_%'
                            AND c.TypeID1 = 1
                            AND c.Service = 1
                            AND c.Country = ?
                            AND ch.Lvl BETWEEN ? AND ?
                        """,
                            (country_code, min_level, max_level),
                        )

                monsters = cursor.fetchall()
                for monster_id, monster_level in monsters:
                    # Calculate adjusted drop ratio based on level threshold
                    if self.level_threshold > 0 and self.decrease_pct > 0 and monster_level > self.level_threshold:
                        # Monster is above threshold, apply decreasing probability
                        levels_above_threshold = monster_level - self.level_threshold
                        decrease_factor = (1 - self.decrease_pct / 100) ** levels_above_threshold
                        adjusted_drop_ratio = drop_ratio * decrease_factor

                        # Add minimum floor to prevent extremely tiny values
                        # Never go below 1% of base rate
                        adjusted_drop_ratio = max(adjusted_drop_ratio, drop_ratio * 0.01)
                    else:
                        # Monster at or below threshold, or feature disabled
                        adjusted_drop_ratio = drop_ratio

                    assignments.append(
                        (1, monster_id, group_id, group_name, 0, 1, 1, adjusted_drop_ratio, 0, 0)
                    )

                if len(created_groups) % 10 == 0:
                    self.progress.emit(
                        f"Planned assignments for {len(assignments)} monster-group pairs"
                    )

            # Insert assignments in batches
            # SQL Server limit: 2100 parameters per query
            # 10 columns per row = 2100/10 = 210 rows max
            batch_size = 200  # Use 200 for safety margin
            inserted_assignments = 0
            for i in range(0, len(assignments), batch_size):
                batch = assignments[i : i + batch_size]

                values_parts = []
                params = []
                for (
                    service,
                    monster_id,
                    group_id,
                    group_name,
                    overlap,
                    drop_min,
                    drop_max,
                    drop_ratio,
                    p1,
                    p2,
                ) in batch:
                    values_parts.append("(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
                    params.extend(
                        [
                            service,
                            monster_id,
                            group_id,
                            group_name,
                            overlap,
                            drop_min,
                            drop_max,
                            drop_ratio,
                            p1,
                            p2,
                        ]
                    )

                values_clause = ",".join(values_parts)
                insert_sql = f"""
                    INSERT INTO _RefMonster_AssignedItemRndDrop
                    (Service, RefMonsterID, RefItemGroupID, ItemGroupCodeName128, Overlap, DropAmountMin, DropAmountMax, DropRatio, param1, param2)
                    VALUES {values_clause}
                """
                cursor.execute(insert_sql, params)
                inserted_assignments += len(batch)

                # Update progress
                percentage = 40 + int((inserted_assignments / len(assignments)) * 50)
                elapsed_time = time.time() - start_time
                if inserted_assignments > 0:
                    time_per_insert = elapsed_time / inserted_assignments
                    remaining = len(assignments) - inserted_assignments
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
                    f"Created {inserted_assignments}/{len(assignments)} assignments"
                )

            self.progress.emit(
                f"Step 5 complete: Created {len(assignments)} assignments"
            )
            self.progress_percent.emit(90, "Committing...")

            # Step 6: Commit changes
            self.progress.emit("Step 6/6: Committing changes to database...")
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
                f"Drop groups created: {len(created_groups)}\n"
                f"Monster-group assignments: {len(assignments)}"
            )

            self.finished.emit(True, summary)

        except Exception as e:
            import traceback

            error_details = traceback.format_exc()
            self.finished.emit(False, f"Error: {str(e)}\n\nDetails:\n{error_details}")


class RareDropTool(QMainWindow):
    CONFIG_FILE = "db_config.json"

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Rare Item Drop Probability Tool v{__VERSION__}")
        self.setGeometry(100, 100, 600, 720)
        self.setMinimumSize(600, 720)  # Prevent window from being too small

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

        # Version label
        version_label = QLabel(f"Version {__VERSION__}")
        version_label.setStyleSheet("font-size: 9pt; color: #666; padding-bottom: 5px;")
        version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version_label)

        # Form layout for inputs
        form_layout = QFormLayout()
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form_layout.setVerticalSpacing(18)
        form_layout.setHorizontalSpacing(15)
        form_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        # Increase label font size
        label_style = "font-size: 11pt;"

        # Star (A_RARE)
        star_label = QLabel("Seal of Star:")
        star_label.setStyleSheet(label_style)
        star_layout = QHBoxLayout()
        star_layout.setSpacing(10)
        star_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.star_checkbox = QCheckBox("Enable")
        self.star_checkbox.setChecked(True)
        self.star_checkbox.setStyleSheet("font-size: 11pt;")
        self.star_checkbox.setFixedWidth(80)
        self.star_prob_input = QLineEdit("0.01")
        self.star_prob_input.setPlaceholderText("0.01 for 1%")
        self.star_prob_input.setMinimumHeight(35)
        self.star_prob_input.setFixedWidth(200)
        self.star_prob_input.setStyleSheet("font-size: 12pt; padding: 5px;")
        star_layout.addWidget(self.star_checkbox, 0, Qt.AlignmentFlag.AlignVCenter)
        star_layout.addWidget(self.star_prob_input, 0, Qt.AlignmentFlag.AlignVCenter)
        star_layout.addStretch()
        form_layout.addRow(star_label, star_layout)

        # Moon (B_RARE)
        moon_label = QLabel("Seal of Moon:")
        moon_label.setStyleSheet(label_style)
        moon_layout = QHBoxLayout()
        moon_layout.setSpacing(10)
        moon_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.moon_checkbox = QCheckBox("Enable")
        self.moon_checkbox.setChecked(True)
        self.moon_checkbox.setStyleSheet("font-size: 11pt;")
        self.moon_checkbox.setFixedWidth(80)
        self.moon_prob_input = QLineEdit("0.005")
        self.moon_prob_input.setPlaceholderText("0.01 for 1%")
        self.moon_prob_input.setMinimumHeight(35)
        self.moon_prob_input.setFixedWidth(200)
        self.moon_prob_input.setStyleSheet("font-size: 12pt; padding: 5px;")
        moon_layout.addWidget(self.moon_checkbox, 0, Qt.AlignmentFlag.AlignVCenter)
        moon_layout.addWidget(self.moon_prob_input, 0, Qt.AlignmentFlag.AlignVCenter)
        moon_layout.addStretch()
        form_layout.addRow(moon_label, moon_layout)

        # Sun (C_RARE)
        sun_label = QLabel("Seal of Sun:")
        sun_label.setStyleSheet(label_style)
        sun_layout = QHBoxLayout()
        sun_layout.setSpacing(10)
        sun_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.sun_checkbox = QCheckBox("Enable")
        self.sun_checkbox.setChecked(True)
        self.sun_checkbox.setStyleSheet("font-size: 11pt;")
        self.sun_checkbox.setFixedWidth(80)
        self.sun_prob_input = QLineEdit("0.001")
        self.sun_prob_input.setPlaceholderText("0.01 for 1%")
        self.sun_prob_input.setMinimumHeight(35)
        self.sun_prob_input.setFixedWidth(200)
        self.sun_prob_input.setStyleSheet("font-size: 12pt; padding: 5px;")
        sun_layout.addWidget(self.sun_checkbox, 0, Qt.AlignmentFlag.AlignVCenter)
        sun_layout.addWidget(self.sun_prob_input, 0, Qt.AlignmentFlag.AlignVCenter)
        sun_layout.addStretch()
        form_layout.addRow(sun_label, sun_layout)

        # Level distance
        distance_label = QLabel("Level Distance (±):")
        distance_label.setStyleSheet(label_style)
        self.level_distance_input = QLineEdit("10")
        self.level_distance_input.setPlaceholderText("e.g., 10")
        self.level_distance_input.setMinimumHeight(35)
        self.level_distance_input.setFixedWidth(200)
        self.level_distance_input.setStyleSheet("font-size: 12pt; padding: 5px;")
        form_layout.addRow(distance_label, self.level_distance_input)

        # Level threshold for decreasing probability
        threshold_label = QLabel("Level Threshold:")
        threshold_label.setStyleSheet(label_style)
        self.level_threshold_input = QLineEdit(self.saved_level_threshold)
        self.level_threshold_input.setPlaceholderText("e.g., 100")
        self.level_threshold_input.setMinimumHeight(35)
        self.level_threshold_input.setFixedWidth(200)
        self.level_threshold_input.setStyleSheet("font-size: 12pt; padding: 5px;")
        threshold_tooltip = (
            "Monster level threshold for probability decrease.\n"
            "Monsters at or below this level get full base probability.\n"
            "Monsters above this level get decreasing probability.\n"
            "\n"
            "Set to 0 to disable (all monsters get base rate)."
        )
        self.level_threshold_input.setToolTip(threshold_tooltip)
        form_layout.addRow(threshold_label, self.level_threshold_input)

        # Decrease per level with Show Probabilities button
        decrease_label = QLabel("Decrease per Level (%):")
        decrease_label.setStyleSheet(label_style)
        decrease_layout = QHBoxLayout()
        decrease_layout.setSpacing(10)
        decrease_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.decrease_input = QLineEdit(self.saved_decrease_pct)
        self.decrease_input.setPlaceholderText("0-100")
        self.decrease_input.setMinimumHeight(35)
        self.decrease_input.setFixedWidth(200)
        self.decrease_input.setStyleSheet("font-size: 12pt; padding: 5px;")
        decrease_tooltip = (
            "Compound percentage decrease per level above threshold.\n"
            "Examples with threshold=100, decrease=10%:\n"
            "  Level 100 (at threshold): 100% of base rate\n"
            "  Level 101 (1 above): 90% of base rate\n"
            "  Level 105 (5 above): 59% of base rate\n"
            "  Level 110 (10 above): 35% of base rate\n"
            "\n"
            "Set to 0 to disable (all monsters get same rate)."
        )
        self.decrease_input.setToolTip(decrease_tooltip)
        decrease_layout.addWidget(self.decrease_input, 0, Qt.AlignmentFlag.AlignVCenter)

        self.show_prob_button = QPushButton("Show Probabilities")
        self.show_prob_button.setMinimumHeight(35)
        self.show_prob_button.setStyleSheet("font-size: 11pt; padding: 5px;")
        self.show_prob_button.clicked.connect(self.show_probability_dialog)
        decrease_layout.addWidget(self.show_prob_button, 0, Qt.AlignmentFlag.AlignVCenter)
        decrease_layout.addStretch()
        form_layout.addRow(decrease_label, decrease_layout)

        # Region mixture
        region_label = QLabel("Region Mixture:")
        region_label.setStyleSheet(label_style)
        region_layout = QHBoxLayout()
        region_layout.setSpacing(10)
        region_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.country_mixture_checkbox = QCheckBox("Allow cross-region drops")
        self.country_mixture_checkbox.setChecked(True)
        self.country_mixture_checkbox.setStyleSheet("font-size: 11pt;")
        self.country_mixture_checkbox.setMinimumHeight(35)
        self.country_mixture_checkbox.setToolTip(
            "When enabled, monsters can drop items from any region.\n"
            "When disabled, monsters only drop items from the same region.\n"
            "Regions: Chinese (countries 0, 3), European (country 1)"
        )
        region_layout.addWidget(self.country_mixture_checkbox, 0, Qt.AlignmentFlag.AlignVCenter)
        region_layout.addStretch()
        form_layout.addRow(region_label, region_layout)

        layout.addLayout(form_layout)

        # Info label
        info_label = QLabel(
            "Items will be assigned to monsters within ± the level distance.\n"
            "Example: A level 100 item with distance 10 drops from level 90-110 monsters.\n\n"
            "Region mixture: When enabled, monsters can drop items from any region.\n"
            "When disabled, Chinese monsters (countries 0, 3) drop Chinese items,\n"
            "and European monsters (country 1) drop European items."
        )
        info_label.setStyleSheet(
            "padding: 10px; background-color: #e7f3ff; border-radius: 5px;"
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        # Progress bar with ETA (initially shows empty space to prevent layout shift)
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximum(100)
        self.progress_bar.setFixedHeight(25)
        self.progress_bar.setTextVisible(False)  # Hide text initially
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        # ETA label (initially shows empty space to prevent layout shift)
        self.eta_label = QLabel(" ")  # Single space to maintain height
        self.eta_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.eta_label.setStyleSheet(
            "padding: 5px; font-weight: bold; min-height: 20px;"
        )
        layout.addWidget(self.eta_label)

        # Button layout - utility buttons
        button_layout = QHBoxLayout()

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
        self.backup_button = QPushButton("Update Backup")
        self.backup_button.clicked.connect(self.create_backup)
        self.backup_button.setStyleSheet("padding: 8px;")
        self.backup_button.setToolTip(
            "Manually update the backup with current configuration (auto-created on apply)"
        )
        backup_layout.addWidget(self.backup_button)

        # Restore button
        self.restore_button = QPushButton("Restore from Backup")
        self.restore_button.clicked.connect(self.restore_backup)
        self.restore_button.setStyleSheet("padding: 8px;")
        backup_layout.addWidget(self.restore_button)

        layout.addLayout(backup_layout)

        # Apply button layout - main action
        apply_layout = QHBoxLayout()

        # Apply button
        self.apply_button = QPushButton("Apply Drop Rates")
        self.apply_button.clicked.connect(self.apply_drop_rates)
        self.apply_button.setStyleSheet("padding: 10px; font-size: 12pt;")
        apply_layout.addWidget(self.apply_button)

        layout.addLayout(apply_layout)

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

        # Load existing configuration from database if available
        self.load_existing_config()

    def load_config(self):
        """Load database configuration from file or use defaults."""
        default_config = {
            "server": "localhost",
            "port": 1433,
            "database": "SRO_VT_SHARD",
            "user": "sa",
            "password": "",
            "level_threshold": "0",
            "decrease_pct": "0",
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
                self.saved_level_threshold = config.get("level_threshold", default_config["level_threshold"])
                self.saved_decrease_pct = config.get("decrease_pct", default_config["decrease_pct"])
            except Exception:
                # If config file is corrupted, use defaults
                self.server = default_config["server"]
                self.port = default_config["port"]
                self.database = default_config["database"]
                self.user = default_config["user"]
                self.password = default_config["password"]
                self.saved_level_threshold = default_config["level_threshold"]
                self.saved_decrease_pct = default_config["decrease_pct"]
        else:
            # Use defaults
            self.server = default_config["server"]
            self.port = default_config["port"]
            self.database = default_config["database"]
            self.user = default_config["user"]
            self.password = default_config["password"]
            self.saved_level_threshold = default_config["level_threshold"]
            self.saved_decrease_pct = default_config["decrease_pct"]

    def save_config(self):
        """Save database configuration to file."""
        config = {
            "server": self.server,
            "port": self.port,
            "database": self.database,
            "user": self.user,
            "password": self.password,
            "level_threshold": self.level_threshold_input.text() if hasattr(self, 'level_threshold_input') else "0",
            "decrease_pct": self.decrease_input.text() if hasattr(self, 'decrease_input') else "0",
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

    def show_probability_dialog(self):
        """Show probability calculations in a dialog window."""
        try:
            threshold = int(self.level_threshold_input.text().strip() or "0")
            decrease = float(self.decrease_input.text().strip() or "0")

            # Get base probabilities for enabled rare types
            rare_probabilities = {}
            rare_names = {}

            if self.star_checkbox.isChecked():
                try:
                    rare_probabilities['Star'] = float(self.star_prob_input.text().strip() or "0")
                    rare_names['Star'] = 'Seal of Star'
                except ValueError:
                    pass

            if self.moon_checkbox.isChecked():
                try:
                    rare_probabilities['Moon'] = float(self.moon_prob_input.text().strip() or "0")
                    rare_names['Moon'] = 'Seal of Moon'
                except ValueError:
                    pass

            if self.sun_checkbox.isChecked():
                try:
                    rare_probabilities['Sun'] = float(self.sun_prob_input.text().strip() or "0")
                    rare_names['Sun'] = 'Seal of Sun'
                except ValueError:
                    pass

            if not rare_probabilities:
                QMessageBox.information(
                    self,
                    "No Rare Types Enabled",
                    "Please enable at least one rare type to see probability calculations."
                )
                return

            # Create dialog
            dialog = QDialog(self)
            dialog.setWindowTitle("Drop Probability Calculations")
            dialog.setMinimumSize(800, 600)
            layout = QVBoxLayout(dialog)
            layout.setSpacing(15)
            layout.setContentsMargins(20, 20, 20, 20)

            # Add title
            title_label = QLabel("Drop Probability by Monster Level")
            title_label.setStyleSheet("font-size: 16pt; font-weight: bold; color: #2c3e50; padding: 10px;")
            layout.addWidget(title_label)

            # Create text display for probability table
            prob_text = QTextEdit()
            prob_text.setReadOnly(True)
            prob_text.setStyleSheet("""
                QTextEdit {
                    font-family: 'Courier New', Courier, monospace;
                    font-size: 11pt;
                    background-color: #f8f9fa;
                    border: 2px solid #dee2e6;
                    border-radius: 8px;
                    padding: 15px;
                    color: #212529;
                }
            """)

            if threshold <= 0 or decrease <= 0:
                # Show base rates when disabled
                lines = ["Probability Decrease: DISABLED\n"]
                lines.append("Monster Level  | " + " | ".join([f"{rare_names[r]:19s}" for r in sorted(rare_probabilities.keys())]))
                lines.append("=" * (15 + len(rare_probabilities) * 24))

                # Show levels from 10 to 140 in steps of 10
                example_levels = list(range(10, 150, 10))
                for level in example_levels:
                    probs = []
                    for rare_type in sorted(rare_probabilities.keys()):
                        base_prob = rare_probabilities[rare_type]
                        probs.append(f"{base_prob:.6f} ({base_prob*100:.4f}%)")
                    lines.append(f"Level {level:3d}     | " + " | ".join(probs))

                prob_text.setPlainText("\n".join(lines))
            else:
                # Calculate examples in steps of 10 levels up to 140
                levels = list(range(threshold, 150, 10))
                if threshold not in levels:
                    levels = [threshold] + levels

                lines = [f"Probability Decrease: ENABLED (Threshold: {threshold}, Decrease: {decrease}% per level)\n"]
                lines.append("Monster Level  | " + " | ".join([f"{rare_names[r]:19s}" for r in sorted(rare_probabilities.keys())]))
                lines.append("=" * (15 + len(rare_probabilities) * 24))

                for level in levels:
                    if level > threshold:
                        levels_above = level - threshold
                        decrease_factor = (1 - decrease / 100) ** levels_above
                    else:
                        decrease_factor = 1.0

                    probs = []
                    for rare_type in sorted(rare_probabilities.keys()):
                        base_prob = rare_probabilities[rare_type]
                        actual_prob = base_prob * decrease_factor
                        # Ensure minimum floor of 1% of base
                        actual_prob = max(actual_prob, base_prob * 0.01)
                        probs.append(f"{actual_prob:.6f} ({actual_prob*100:.4f}%)")

                    level_label = f"Level {level:3d}"
                    if level > threshold:
                        level_label += f" (+{levels_above:2d})"
                    else:
                        level_label += "     "

                    lines.append(level_label + " | " + " | ".join(probs))

                prob_text.setPlainText("\n".join(lines))

            layout.addWidget(prob_text)

            # Add info footer
            info_label = QLabel("Note: Probabilities shown are actual drop rates that will be applied to the database.")
            info_label.setStyleSheet("color: #6c757d; font-size: 9pt; font-style: italic; padding: 5px;")
            layout.addWidget(info_label)

            # Add close button
            button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
            button_box.setStyleSheet("""
                QPushButton {
                    background-color: #007bff;
                    color: white;
                    border: none;
                    padding: 8px 20px;
                    border-radius: 4px;
                    font-size: 10pt;
                    min-width: 80px;
                }
                QPushButton:hover {
                    background-color: #0056b3;
                }
                QPushButton:pressed {
                    background-color: #004085;
                }
            """)
            button_box.accepted.connect(dialog.accept)
            layout.addWidget(button_box)

            dialog.exec()

        except (ValueError, ZeroDivisionError):
            QMessageBox.warning(
                self,
                "Invalid Input",
                "Please enter valid numbers for probabilities and decrease settings."
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

            # Check if both backup tables exist
            cursor.execute("""
                SELECT COUNT(*)
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_NAME IN ('_RefDropItemGroup_Backup', '_RefMonster_AssignedItemRndDrop_Backup')
            """)
            backup_exists = cursor.fetchone()[0] >= 2  # Both tables must exist

            conn.close()

            if not backup_exists:
                # No backup - will be created automatically when user applies changes
                self.status_label.setText(
                    "No backup yet - Will be created automatically on first apply"
                )
                self.status_label.setStyleSheet(
                    "padding: 10px; background-color: #e7f3ff; color: #004085;"
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

    def load_existing_config(self):
        """Load existing rare drop configuration from database and populate UI."""
        try:
            conn = mssql_python.connect(self.get_connection_string())
            cursor = conn.cursor()

            # Query existing rare drop assignments to detect current configuration
            cursor.execute("""
                SELECT DISTINCT ItemGroupCodeName128, DropRatio
                FROM _RefMonster_AssignedItemRndDrop
                WHERE ItemGroupCodeName128 LIKE 'RARE_%'
            """)

            results = cursor.fetchall()
            conn.close()

            if not results:
                # No existing configuration found
                self.status_label.setText(
                    "No existing configuration detected - Using defaults"
                )
                self.status_label.setStyleSheet(
                    "padding: 10px; background-color: #e7f3ff; color: #004085;"
                )
                return

            # Parse results to extract drop ratios by rare type
            rare_configs = {"A": set(), "B": set(), "C": set()}
            has_region_code = False

            for group_name, drop_ratio in results:
                # Group names are like: RARE_A_LVL_50, or RARE_A_LVL_50_CN, RARE_A_LVL_50_EU, RARE_A_LVL_50_R2, etc.
                # Check if any group name contains region code (e.g., _CN, _EU, _R2)
                parts = group_name.split("_")
                if len(parts) > 4:  # RARE_A_LVL_50 has 4 parts, with region it has 5+
                    last_part = parts[-1]
                    # Check for CN, EU, or R<digit> patterns
                    if last_part in ("CN", "EU") or (last_part.startswith("R") and last_part[1:].isdigit()):
                        has_region_code = True

                if "RARE_A_" in group_name:
                    rare_configs["A"].add(drop_ratio)
                elif "RARE_B_" in group_name:
                    rare_configs["B"].add(drop_ratio)
                elif "RARE_C_" in group_name:
                    rare_configs["C"].add(drop_ratio)

            # Update country mixture checkbox based on detection
            # If groups have region codes, country mixture is disabled
            self.country_mixture_checkbox.setChecked(not has_region_code)

            # Update UI with detected values
            config_summary = []

            # Seal of Star (A_RARE)
            if rare_configs["A"]:
                # If all groups have the same ratio (expected), just take any one
                # Convert set to list and take first value
                star_ratio = list(rare_configs["A"])[0]
                # Format with up to 6 significant figures, removing trailing zeros
                star_ratio_str = f"{star_ratio:.6g}"
                self.star_checkbox.setChecked(True)
                self.star_prob_input.setText(star_ratio_str)
                config_summary.append(f"Star: {star_ratio_str}")
            else:
                self.star_checkbox.setChecked(False)

            # Seal of Moon (B_RARE)
            if rare_configs["B"]:
                moon_ratio = list(rare_configs["B"])[0]
                moon_ratio_str = f"{moon_ratio:.6g}"
                self.moon_checkbox.setChecked(True)
                self.moon_prob_input.setText(moon_ratio_str)
                config_summary.append(f"Moon: {moon_ratio_str}")
            else:
                self.moon_checkbox.setChecked(False)

            # Seal of Sun (C_RARE)
            if rare_configs["C"]:
                sun_ratio = list(rare_configs["C"])[0]
                sun_ratio_str = f"{sun_ratio:.6g}"
                self.sun_checkbox.setChecked(True)
                self.sun_prob_input.setText(sun_ratio_str)
                config_summary.append(f"Sun: {sun_ratio_str}")
            else:
                self.sun_checkbox.setChecked(False)

            # Try to detect level distance from existing assignments
            level_distance = self.detect_level_distance()
            if level_distance is not None:
                self.level_distance_input.setText(str(level_distance))
                config_summary.append(f"Level ±{level_distance}")

            # Add region mixture status to summary
            if has_region_code:
                config_summary.append("Region-aware")
            else:
                config_summary.append("Region mixture")

            # Update status label with detected configuration
            if config_summary:
                summary_text = ", ".join(config_summary)
                self.status_label.setText(f"Loaded existing config: {summary_text}")
                self.status_label.setStyleSheet(
                    "padding: 10px; background-color: #d4edda; color: #155724;"
                )
            else:
                self.status_label.setText("No rare drops configured in database")
                self.status_label.setStyleSheet(
                    "padding: 10px; background-color: #e7f3ff; color: #004085;"
                )

        except Exception:
            # Don't fail startup if we can't load existing config
            # Just use defaults silently or show a subtle warning
            pass

    def detect_level_distance(self):
        """Detect level distance from existing RARE_* assignments."""
        try:
            conn = mssql_python.connect(self.get_connection_string())
            cursor = conn.cursor()

            # For each unique group, get the min and max monster levels assigned to it
            # This will accurately reflect the configured level_distance parameter
            cursor.execute("""
                SELECT
                    a.ItemGroupCodeName128,
                    MIN(ch.Lvl) as MinMonsterLevel,
                    MAX(ch.Lvl) as MaxMonsterLevel
                FROM _RefMonster_AssignedItemRndDrop a
                JOIN _RefObjCommon c ON a.RefMonsterID = c.ID
                JOIN _RefObjChar ch ON c.Link = ch.ID
                WHERE a.ItemGroupCodeName128 LIKE 'RARE_%'
                GROUP BY a.ItemGroupCodeName128
            """)

            results = cursor.fetchall()
            conn.close()

            if not results:
                return None

            # Calculate distance based on monster level range for each group
            # Filter out edge cases (very low or very high level items)
            distances = []
            for group_name, min_monster_level, max_monster_level in results:
                # Extract level from group name: RARE_A_LVL_50 → 50 or RARE_A_LVL_50_CN → 50
                try:
                    # Split by _LVL_ and then take everything before the next underscore or region code
                    parts = group_name.split("_LVL_")
                    if len(parts) >= 2:
                        # Handle both RARE_A_LVL_50 and RARE_A_LVL_50_CN formats
                        level_part = parts[1].split("_")[0]  # Takes "50" from "50" or "50_CN"
                        item_level = int(level_part)

                        # Filter out edge cases (items at extreme levels where monster coverage is incomplete)
                        # Focus on items in the middle range (20-110) for accurate detection
                        if 20 <= item_level <= 110:
                            # Calculate distance from item level to furthest assigned monster
                            # This correctly handles asymmetric ranges (e.g., when max(0, level-D) is used)
                            distance_from_min = abs(item_level - min_monster_level)
                            distance_from_max = abs(item_level - max_monster_level)
                            estimated_distance = max(distance_from_min, distance_from_max)
                            distances.append(estimated_distance)
                except (ValueError, IndexError):
                    continue

            if distances:
                # Use mode (most common distance) to get the configured value
                from collections import Counter
                distance_counts = Counter(distances)
                most_common_distance = distance_counts.most_common(1)[0][0]

                return most_common_distance if most_common_distance > 0 else None

            return None

        except Exception:
            # If detection fails, return None to keep default
            return None

    def create_backup(self):
        """Create/update backup of the drop table."""
        reply = QMessageBox.question(
            self,
            "Update Backup",
            "This will update the backup with the current database state.\n\n"
            "Note: Backups are automatically created before applying changes,\n"
            "but you can manually update the backup here if needed.\n\n"
            "Backup tables:\n"
            "• _RefDropItemGroup_Backup\n"
            "• _RefMonster_AssignedItemRndDrop_Backup\n\n"
            "⚠️  ALL rows will be backed up (not just RARE_* entries).\n\n"
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
        self.progress_bar.setMaximum(0)  # Indeterminate
        self.progress_bar.setTextVisible(True)
        self.eta_label.setText("Processing...")
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
            "⚠️  WARNING: This will restore the database to the backed up state!\n\n"
            "• ALL current data in both tables will be DELETED\n"
            "• ALL data from backup will be restored\n"
            "• This restores the complete state from when backup was created\n\n"
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
        self.progress_bar.setMaximum(0)  # Indeterminate
        self.progress_bar.setTextVisible(True)
        self.eta_label.setText("Processing...")
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
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.eta_label.setText(" ")

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
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.eta_label.setText(" ")

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

            level_threshold = int(self.level_threshold_input.text().strip())
            if level_threshold < 0:
                raise ValueError("Level threshold must be non-negative")

            decrease_pct = float(self.decrease_input.text().strip())
            if decrease_pct < 0 or decrease_pct > 100:
                raise ValueError("Decrease percentage must be between 0 and 100")

        except ValueError as e:
            QMessageBox.warning(
                self, "Invalid Input", f"Please check your inputs:\n{str(e)}"
            )
            return

        # Confirm action
        types_str = ", ".join([f"{t}_RARE" for t in rare_types])
        country_mixture = self.country_mixture_checkbox.isChecked()
        region_mode = "Cross-region drops enabled" if country_mixture else "Region-aware (Chinese/European separation)"

        reply = QMessageBox.question(
            self,
            "Confirm Action",
            f"This will update drop rates for:\n{types_str}\n\n"
            f"Level distance: ±{level_distance}\n"
            f"Region mode: {region_mode}\n\n"
            f"⚠️  This operation will DELETE existing drop entries for these items.\n\n"
            f"✓  A backup will be created automatically before applying changes.\n"
            f"✓  You can restore from backup at any time.\n\n"
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
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.eta_label.setText("Starting...")
        self.status_label.setText("Processing...")
        self.status_label.setStyleSheet(
            "padding: 10px; background-color: #fff3cd; color: #856404;"
        )

        # Start worker thread
        country_mixture = self.country_mixture_checkbox.isChecked()
        self.worker = DropRateWorker(
            self.get_connection_string(), rare_types, probabilities, level_distance, country_mixture, level_threshold, decrease_pct
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
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.eta_label.setText(" ")

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
