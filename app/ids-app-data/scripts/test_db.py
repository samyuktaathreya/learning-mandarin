import sqlite3
from pathlib import Path

# Path to your clean DB
DB_PATH = Path("../data/clean/characters.db")

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

print("--- TABLE ROW COUNTS ---")
cursor.execute("SELECT COUNT(*) FROM characters")
print(f"Total target characters: {cursor.fetchone()[0]}")

cursor.execute("SELECT COUNT(*) FROM character_components")
print(f"Total component relationships: {cursor.fetchone()[0]}")

print("\n--- SAMPLE CHARACTER DATA ---")
cursor.execute("SELECT * FROM characters LIMIT 5")
for row in cursor.fetchall():
    print(row)

print("\n--- TEST DECOMPOSITION RECURSION ---")
# Pick a character with multiple parts (e.g., '好')
target = "好"

print(f"Decomposition tree for '{target}':")
cursor.execute(
    """
    SELECT depth, position, component_char, frequency_in_corpus 
    FROM character_components 
    WHERE char = ? 
    ORDER BY depth, id
""",
    (target,),
)

results = cursor.fetchall()
if not results:
    print(f"No components found for '{target}' (it might be atomic).")
else:
    for depth, pos, comp, freq in results:
        indent = "  " * depth
        print(f"{indent}└─ [Depth {depth} - {pos}]: {comp} (Appears in {freq} other HSK 1 chars)")
        
conn.close()