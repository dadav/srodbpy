# Silkroad Online Database Tools

This repository contains tools for managing the Silkroad Online database.

## Tools

### 1. Rare Item Drop Probability Tool (`rare_drop_tool.py`)

A GUI tool to configure drop rates for rare items (Seal of Star, Moon, and Sun).

#### Features

- Configure drop probabilities for three rare item types:
  - **Seal of Star** (\*\_A_RARE items)
  - **Seal of Moon** (\*\_B_RARE items)
  - **Seal of Sun** (\*\_C_RARE items)
- Set individual probability for each type (e.g., 0.01 = 1%)
- Configure level distance for monster assignment
- Items drop from monsters within ± level distance of the item's required level

#### Usage

1. Run the tool:

   ```bash
   uv run python rare_drop_tool.py
   ```

2. Configure the settings:
   - Check the boxes for the rare types you want to enable
   - Enter the drop probability for each (0.01 = 1%, 0.005 = 0.5%, etc.)
   - Set the level distance (e.g., 10 means items drop from monsters ±10 levels)

3. Click "Test Connection" to verify database connectivity

4. Click "Apply Drop Rates" to update the database

#### Example

If you set:

- Seal of Star: Enabled, 0.01 (1%)
- Level Distance: 10

The tool will:

1. Find all \*\_A_RARE items in the database
2. For each item (e.g., a level 100 sword), find monsters level 90-110
3. Assign the item to those monsters with 1% drop rate

#### Warning

This tool will DELETE existing drop entries for the configured rare items before creating new ones. Make sure to backup your database before using this tool.

### 2. Monster Item Assigner (`main.py`)

A GUI tool to manually assign items to specific monsters.

#### Usage

```bash
uv run python main.py
```

## Database Configuration

Both tools connect to:

- Server: localhost:1433
- Database: SRO_VT_SHARD
- User: sa
- Password: Foobarfoobar2

To change these settings, edit the connection parameters in the respective Python files.

## Installation

1. Install dependencies:

   ```bash
   uv sync
   ```

2. Ensure your MSSQL database is running and accessible

## Development

### Database Schema

Key tables used by these tools:

- **\_RefObjCommon**: All game objects (items, monsters, NPCs)
  - ID: Object ID
  - CodeName128: Internal name
  - TypeID1, TypeID2, TypeID3, TypeID4: Object type classification
  - ReqLevel1: Required level for items

- **\_RefObjChar**: Character/monster properties
  - ID: References \_RefObjCommon.ID
  - Lvl: Monster level

- **\_RefMonster_AssignedItemDrop**: Drop assignments
  - RefMonsterID: Monster ID
  - RefItemID: Item ID
  - DropRatio: Drop probability (0.01 = 1%)

### Monster Identification

Monsters have:

- TypeID1 = 1
- TypeID2 = 2

### Rare Item Patterns

- Seal of Star: \*\_A_RARE
- Seal of Moon: \*\_B_RARE
- Seal of Sun: \*\_C_RARE
