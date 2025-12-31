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

**Monster-specific group approach** (NEW - v0.5.0+): To fix drop rate multiplication bug, the tool now:
1. Creates drop groups organized by (monster_id, rare_type) - e.g., RARE_A_MOB_12345
2. Each group contains ALL items within the monster's level range (level ± distance) with equal SelectRatio
3. Assigns exactly 3 groups per monster (one for each rare type: Star/A, Moon/B, Sun/C)
4. Ensures correct drop rates - no multiplication when level_distance > 0

**Why this change?** The old level-based grouping (RARE_A_LVL_50) caused a critical bug:
- When level_distance > 0, monsters received multiple overlapping group assignments
- Example: Monster at level 50 with distance=1 received RARE_A_LVL_49, RARE_A_LVL_50, and RARE_A_LVL_51
- Result: 3× configured drop rate instead of 1× (drop rate multiplication bug)
- The new approach assigns exactly one group per monster+type, eliminating multiplication

**Database size impact**:
- Old approach: ~180 groups, ~1,080 group entries, ~84,000 assignments (with distance=10)
- New approach: ~12,000 groups, ~504,000 group entries, ~12,000 assignments
- Net increase: ~500KB (6× more rows, but acceptable in absolute terms)
- Trade-off: Larger database, but correct drop rates

**Critical optimization**: The DropRateWorker uses batch operations:
1. Collect items and organize by level for quick lookup
2. Query all monsters ONCE with their levels and regions
3. DELETE old groups and assignments (LIKE 'RARE_%')
4. For each monster, create 3 groups (Star, Moon, Sun) containing items within level distance
5. CREATE drop groups in _RefDropItemGroup (batches of 300 rows)
6. CREATE assignments in _RefMonster_AssignedItemRndDrop (batches of 200 rows)
7. Processing time: 3-5 minutes for ~4,000 monsters (acceptable performance)

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

### Monster-Specific Assignment (v0.5.0+)
For each monster M at level L with level distance D:
- Create 3 groups (one per enabled rare type: Star/A, Moon/B, Sun/C)
- Each group contains items from levels `(L - D)` to `(L + D)` of that rare type
- Group naming: `RARE_{type}_MOB_{monster_id}` or `RARE_{type}_MOB_{monster_id}_{region}`
- Assign each group to its owner monster with configured drop probability
- Level threshold degradation: If enabled, monsters above threshold get reduced drop rates
- When a monster drops a group, game selects one item randomly based on SelectRatio (all items have equal probability)

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
3. Detects level distance by analyzing item level ranges (line 1396-1521):
   - **New format (v0.5.0+)**: Samples 50 random RARE_*_MOB_* groups
     - Extracts monster ID from group name
     - Queries item levels within each group
     - Calculates distance as max deviation from monster level
     - Uses mode (most common distance) across samples
   - **Old format (backward compatibility)**: Analyzes RARE_*_LVL_* groups
     - Extracts item level from group name (e.g., RARE_A_LVL_50 → 50)
     - Joins with monster levels via `_RefObjChar`
     - Calculates max level difference
4. Populates UI fields: checkboxes, probability inputs, and level distance
5. Displays configuration summary in status label (e.g., "Star: 0.01, Moon: 0.005, Sun: 0.001, Level ±10")

This allows the tool to "remember" previous settings by reading them from the database, making it easier to make incremental adjustments without re-entering all values. The detection logic supports both old (level-based) and new (monster-specific) group formats for backward compatibility.

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
