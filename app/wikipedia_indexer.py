import bz2
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path
import time
import sys
import os

def index_wikipedia(bz2_path: str, db_path: str, limit: int = None):
    bz2_path = Path(bz2_path)
    db_path = Path(db_path)
    
    print(f"Starting indexing of {bz2_path}...")
    print(f"Output database: {db_path}")

    # Connect to SQLite
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Enable WAL mode for concurrent reading/writing
    cursor.execute("PRAGMA journal_mode=WAL")
    
    # Create tables IF NOT EXISTS (Supports Resuming)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT UNIQUE,
            content TEXT
        )
    """)
    # Create Full Text Search index IF NOT EXISTS
    cursor.execute("CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(title, content, content='articles', content_rowid='id')")
    
    conn.commit()

    # Check for existing progress
    last_title = None
    try:
        cursor.execute("SELECT title FROM articles ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        if row:
            last_title = row[0]
            print(f"Found existing index. Resuming after: '{last_title}'")
    except Exception as e:
        print(f"Could not check existing index: {e}")

    count = 0
    skipping = True if last_title else False
    start_time = time.time()
    batch_size = 100
    
    # Track performance
    processed_since_start = 0

    try:
        with bz2.open(bz2_path, "rt", encoding="utf-8") as f:
            # We skip the namespace entirely and just look for local names
            context = ET.iterparse(f, events=("end",))
            for event, elem in context:
                # Strip namespace from tag
                tag_name = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                
                if tag_name == "page":
                    title = None
                    content = None
                    
                    # Find title and text inside the page element
                    for child in elem:
                        child_name = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                        if child_name == "title":
                            title = child.text
                        elif child_name == "revision":
                            for rev_child in child:
                                rev_child_name = rev_child.tag.split("}")[-1] if "}" in rev_child.tag else rev_child.tag
                                if rev_child_name == "text":
                                    content = rev_child.text
                    
                    if title:
                        # RESUME LOGIC
                        if skipping:
                            if title == last_title:
                                print(f"\nFound resume point: '{title}'. Starting indexing...")
                                skipping = False
                            
                            if count % 1000 == 0:
                                print(f"Skipping... currently at '{title}'", end="\r")
                            
                            elem.clear()
                            continue
                            
                        # NORMAL INDEXING LOGIC (from here down)
                        if content:
                            content_lower = content.lower().strip()
                            # Skip redirects/json
                            if content_lower.startswith("#redirect") or content_lower.startswith("{"):
                                elem.clear()
                                continue

                            # Use a single transaction for both inserts
                            try:
                                cursor.execute("INSERT OR IGNORE INTO articles (title, content) VALUES (?, ?)", (title, content))
                                row_id = cursor.lastrowid # This will be 0 if IGNORE triggered
                                
                                # Manually fetch rowid if it was ignored but we want to ensure FTS consistency? 
                                # No, if ignored, it's already there. 
                                # But wait, if we are resuming, we shouldn't hit duplicates unless the last batch failed partially.
                                if row_id:
                                    cursor.execute("INSERT INTO articles_fts (rowid, title, content) VALUES (?, ?, ?)", (row_id, title, content))
                                    count += 1
                                    processed_since_start += 1
                            except Exception as e:
                                print(f"\nError inserting '{title}': {e}")
                            
                            if processed_since_start % batch_size == 0:
                                conn.commit()
                                elapsed = time.time() - start_time
                                rate = processed_since_start / elapsed if elapsed > 0 else 0
                                print(f"Indexed {processed_since_start} new articles (Total: {count + (cursor.lastrowid or 0)})... ({rate:.1f} art/sec)", end="\r")

                    # Crucial: Clear the element from memory
                    elem.clear()
                    
                    if limit and processed_since_start >= limit:
                        break
        
        conn.commit()
        print(f"\nSuccess! Indexed {processed_since_start} new articles in {time.time() - start_time:.1f}s")

    except KeyboardInterrupt:
        print("\nIndexing interrupted by user. Status saved.")
    except Exception as e:
        print(f"\nError during indexing: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parent.parent
    default_data_dir = Path(os.getenv("WIKI_DATA_DIR", str(repo_root / "external data" / "wikipedia")))
    WIKI_BZ2 = os.getenv(
        "WIKI_BZ2_PATH",
        str(default_data_dir / "enwiki-20260101-pages-articles-multistream.xml.bz2"),
    )
    WIKI_DB = os.getenv("WIKI_DB_PATH", str(default_data_dir / "wikipedia.db"))
    
    limit = None
    if "--test" in sys.argv:
        print("Running in test mode (indexing 5000 articles)...")
        limit = 5000

    index_wikipedia(WIKI_BZ2, WIKI_DB, limit)
