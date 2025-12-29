# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**srodbpy** is a PyQt6 GUI tool for managing Silkroad Online database drop rates for rare items (Seal of Star, Moon, and Sun). It connects to an MSSQL database to configure monster drop probabilities based on level ranges.

## Development Commands

### Setup
```bash
# Install dependencies
uv sync

# Install package in editable mode (required for version detection)
uv pip install -e .
```

### Running
```bash
# Run the application
uv run python main.py
```

### Building Executables
```bash
# Install dev dependencies (includes PyInstaller)
uv sync --group dev

# Build for current platform
# Windows:
uv run pyinstaller sro-rare-drop-tool.spec

# Linux:
uv run pyinstaller sro-rare-drop-tool.spec
```

## Architecture

### Single-File Application
All application code lives in `main.py` (~950 lines). This is intentional - the tool is simple enough to not require modularization.

### Threading Model
The application uses QThread workers to prevent UI blocking during long database operations:

- **BackupWorker** (line 108): Creates database backup in background thread
- **RestoreWorker** (line 154): Restores from backup in background thread
- **DropRateWorker** (line 206): Main worker that processes drop rate updates with optimized batch operations

Workers emit signals (`progress`, `finished`) to update UI. All database operations run in worker threads, never in the main UI thread.

### Database Operations Architecture

The tool operates on five main MSSQL tables:
- `_RefObjCommon`: All game objects (items, monsters, NPCs)
- `_RefObjChar`: Character/monster properties including levels
- `_RefDropItemGroup`: Drop group definitions (group → items with probabilities)
- `_RefMonster_AssignedItemRndDrop`: Group assignments (monster → group + drop probability)

**Group-based approach**: Instead of assigning individual items to monsters, the tool:
1. Creates drop groups organized by (rare_type, level) - e.g., RARE_A_LVL_50
2. Each group contains all rare items at that level with equal SelectRatio
3. Assigns these groups to monsters within the level range
4. Significantly reduces database rows (~180 groups vs ~100,000+ individual assignments)

**Critical optimization**: The DropRateWorker uses batch operations:
1. Collect items and organize by (rare_type, level) combination
2. DELETE old groups and assignments (LIKE 'RARE_%')
3. CREATE drop groups in _RefDropItemGroup (batches of 300 rows)
4. CREATE assignments in _RefMonster_AssignedItemRndDrop (batches of 200 rows)
5. This reduces 4+ hour operations to minutes

**SQL Server constraint**: Batch sizes are limited by SQL Server's 2100 parameter maximum per query:
- _RefDropItemGroup: 6 columns → max 350 rows, using 300 for safety
- _RefMonster_AssignedItemRndDrop: 10 columns → max 210 rows, using 200 for safety

### Configuration System
Database settings stored in `db_config.json` (gitignored). Loaded by `load_config()` (line 556), edited via `DatabaseSettingsDialog` (line 53), saved by `save_config()` (line 590).

### Versioning Strategy
Version defined **only** in `pyproject.toml`. The `get_version()` function (line 26) reads from:
1. Package metadata (`importlib.metadata`) - works for installed packages and PyInstaller
2. Fallback to reading `pyproject.toml` directly - for development
3. Raises error if both fail

**Important**: Always run `uv pip install -e .` after cloning to ensure version is accessible.

### PyInstaller Packaging
Two-file build system:
- `pyinstaller_entrypoint.py`: Sets up environment (library paths, ODBC drivers) before importing main
- `main.py`: Actual application code
- `sro-rare-drop-tool.spec`: PyInstaller configuration with mssql_python native library bundling

The entrypoint handles platform-specific ODBC driver loading for Linux distros (Debian/Ubuntu, RHEL, SUSE, Alpine) and Windows.

## Key Implementation Patterns

### Rare Item Identification
Items identified by CodeName128 patterns:
- Star: `*_A_RARE` (highest tier)
- Moon: `*_B_RARE` (mid tier)
- Sun: `*_C_RARE` (lowest tier)

### Monster Identification
Monsters identified by `CodeName128 LIKE 'MOB_%'` pattern.

### Level-Based Assignment
For each drop group at level L with level distance D:
- Find all monsters with level between `(L - D)` and `(L + D)`
- Assign the entire group to those monsters with configured drop probability
- When a monster drops the group, game selects one item randomly based on SelectRatio

### Automatic Backup System
The tool automatically creates backups BEFORE applying any changes (line 320-342):
- **Automatic on first apply**: When "Apply Drop Rates" is clicked, checks if backup exists
- **If no backup exists**: Creates backup tables automatically before making any changes
- **If backup exists**: Uses existing backup (preserves original state)
- **Manual update**: "Update Backup" button allows manually refreshing backup with current database state

Backup tables:
- `_RefDropItemGroup_Backup`: Complete backup of all drop group definitions
- `_RefMonster_AssignedItemRndDrop_Backup`: Complete backup of all group assignments

**Important: Full table backups**:
- **ALL rows** are backed up from both tables, not just RARE_* entries
- First backup preserves the complete virgin database state
- This allows complete restoration to pre-tool state
- Subsequent manual backups can create new restore points

**Safety measures**:
- Backup is created BEFORE any destructive operations
- Restore validates backup is not empty before proceeding
- Restore deletes ALL current data and restores complete backup (not just RARE_* rows)
- This ensures perfect restoration to the backed up state

This ensures you can always restore to the complete database state before your first change, providing a safety net for experimentation.

### Auto-Configuration Loading
On startup, the tool queries the database to detect existing rare drop configuration (line 856-1025):

**Detection logic:**
1. Queries `_RefMonster_AssignedItemRndDrop` for RARE_* groups
2. Extracts drop ratios for each rare type (A, B, C)
3. Detects level distance by analyzing monster-item level differences (line 1027-1070):
   - Samples 20 RARE_* assignments
   - Extracts item level from group name (e.g., RARE_A_LVL_50 → 50)
   - Joins with monster levels via `_RefObjChar`
   - Calculates max level difference (this was the original level_distance parameter)
4. Populates UI fields: checkboxes, probability inputs, and level distance
5. Displays configuration summary in status label (e.g., "Star: 0.01, Moon: 0.005, Sun: 0.001, Level ±10")

This allows the tool to "remember" previous settings by reading them from the database, making it easier to make incremental adjustments without re-entering all values.

## CI/CD

GitHub Actions workflow (`.github/workflows/release.yml`) builds executables for Windows and Linux on version tags (`v*`):
1. Builds on both platforms using PyInstaller
2. Creates GitHub release with versioned binaries
3. Artifacts: `sro-rare-drop-tool-vX.X.X-windows.exe` and `sro-rare-drop-tool-vX.X.X-linux`

## Important Constraints

- Database operations are destructive: DROP operations clear existing entries before creating new ones
- Always ensure backup exists before making changes
- The tool uses batch operations for performance - individual INSERT/DELETE would take hours
- MSSQL connection requires proper ODBC driver setup (handled by PyInstaller entrypoint on packaged builds)
- Version must be kept in sync in `pyproject.toml` only (single source of truth)
