import bz2
import sqlite3
import xml.etree.ElementTree as ET
import re
import html
from pathlib import Path
from typing import Optional, List, Dict
import logging
import random
from urllib.parse import quote

logger = logging.getLogger(__name__)

class WikipediaReader:
    def __init__(self, file_path: str, db_path: Optional[str] = None):
        self.file_path = Path(file_path)
        self.db_path = Path(db_path) if db_path else self.file_path.with_suffix(".db")
        self.namespace = "{http://www.mediawiki.org/xml/export-0.10/}"

    def has_index(self) -> bool:
        if not self.db_path.exists():
            return False
        try:
            conn = self._connect_db(read_only=True)
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='articles' LIMIT 1")
            row = cursor.fetchone()
            conn.close()
            return row is not None
        except Exception:
            return False

    def _connect_db(self, read_only: bool = True) -> sqlite3.Connection:
        if read_only:
            # immutable=1 avoids file locking/journal writes on read-only mounted volumes.
            return sqlite3.connect(f"file:{self.db_path}?mode=ro&immutable=1", uri=True)
        return sqlite3.connect(self.db_path)

    def _linkify_internal_wikilinks(self, text: str) -> str:
        """
        Convert MediaWiki-style internal links to local article links.
        Example: [[Earth]] or [[Earth|our planet]] -> /wikipedia/article/...
        """
        pattern = re.compile(r"\[\[([^\[\]|]+)(?:\|([^\[\]]+))?\]\]")
        out: list[str] = []
        last = 0

        for match in pattern.finditer(text):
            out.append(html.escape(text[last:match.start()]))

            target_raw = (match.group(1) or "").strip()
            label_raw = (
                (match.group(2) or "").strip()
                if match.group(2) is not None
                else target_raw
            )
            target_page = target_raw.split("#", 1)[0].strip()

            if not target_page or ":" in target_page:
                # Skip special namespaces and invalid targets.
                out.append(html.escape(label_raw or target_raw))
            else:
                href = f"/wikipedia/article/{quote(target_page, safe='')}"
                out.append(
                    f'<a href="{href}" class="wiki-inline-link">{html.escape(label_raw or target_page)}</a>'
                )

            last = match.end()

        out.append(html.escape(text[last:]))
        return "".join(out)

    def render_article_html(self, text: str, limit: Optional[int] = None) -> str:
        source = text or ""
        if limit is not None and limit >= 0 and len(source) > limit:
            source = source[:limit]
        rendered = self._linkify_internal_wikilinks(source)
        return rendered.replace("\n", "<br>")

    def clean_wikitext(self, text: str, preserve_links: bool = False) -> str:
        if not text:
            return ""
        
        # 1. Remove HTML comments
        text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
        
        # 2. Remove Templates (Recursive-ish approach for nested {{ }})
        # We do 5 passes to handle deep nesting like {{Cite|url={{...}}}}
        for _ in range(5):
            text = re.sub(r'\{\{[^\{\}]*?\}\}', '', text, flags=re.DOTALL)
            
        # 3. Remove CSS/Style blocks and JSON blobs
        text = re.sub(r'\{[^\}]*?"[^"]*?":.*\}', '', text, flags=re.DOTALL)
        
        # 4. Remove Files/Images: [[File:name.jpg|thumb|description]]
        # We handle nesting here too [[File:... [[Category...]] ]]
        for _ in range(3):
            text = re.sub(r'\[\[(?:File|Image|Category|Media):[^\[\]]*?\]\]', '', text, flags=re.IGNORECASE | re.DOTALL)
        
        # 5. Simplify Links unless rendering article bodies where we preserve
        # [[...]] for hyperlink conversion in the template context.
        if not preserve_links:
            text = re.sub(r'\[\[(?:[^|\]]*\|)?([^\]]+)\]\]', r'\1', text)
        
        # 6. Remove bold/italic markup
        text = text.replace("'''", "").replace("''", "")
        
        # 7. Clean up section headers: == Header == -> Header
        text = re.sub(r'={2,}\s*(.*?)\s*={2,}', r'\1', text)
        
        # 8. Remove other common tech snippets
        text = re.sub(r'&lt;.*?&gt;', '', text) # Remove escaped tags
        if preserve_links:
            text = re.sub(r'\{|\}', ' ', text)
        else:
            text = re.sub(r'\{|\||\}', ' ', text) # Clean up stray pipe/bracket characters
        
        # 9. Normalize Whitespace and stray characters
        # Remove multiple spaces
        text = re.sub(r' {2,}', ' ', text)
        # Remove multiple newlines
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        # 10. Final pass for broken fragments (e.g. from truncation)
        text = re.sub(r'\{\{.*?$', '', text) # Remove partial template at end
        text = re.sub(r'\[\[.*?$', '', text) # Remove partial link at end
        
        # 11. Remove stray leading characters often left by templates
        text = re.sub(r'^[\s\|\}]+', '', text)
        
        return text.strip()

    def get_article(self, search_title: str) -> Optional[Dict]:
        """
        Retrieves an article. Uses SQLite if indexed, otherwise falls back to slow BZ2 scan.
        If the index is partial and the article isn't there yet, also fall back to BZ2.
        """
        article = None
        if self.has_index():
            article = self._get_article_from_db(search_title)
        
        if not article:
            article = self._get_article_from_bz2(search_title)
            
        if article:
            article["text"] = self.clean_wikitext(article["text"], preserve_links=True)
            
        return article

    def search_articles(self, query: str, limit: int = 10) -> List[Dict]:
        """
        Performs a full-text search. Requires SQLite index.
        """
        if not self.has_index():
            return []
        
        results = []
        try:
            conn = self._connect_db(read_only=True)
            cursor = conn.cursor()
            # Safer FTS query syntax: use subquery or explicit table name for MATCH
            cursor.execute("""
                SELECT a.title, SUBSTR(a.content, 0, 5000) 
                FROM articles a
                JOIN articles_fts f ON a.id = f.rowid
                WHERE f.articles_fts MATCH ? 
                AND a.content NOT LIKE '#REDIRECT%'
                AND a.content NOT LIKE '{%'
                AND a.title NOT LIKE 'Wikipedia:%'
                AND a.title NOT LIKE 'Template:%'
                AND a.title NOT LIKE 'Help:%'
                AND a.title NOT LIKE 'Talk:%'
                LIMIT ?
            """, (query, limit))
            
            for row in cursor.fetchall():
                cleaned = self.clean_wikitext(row[1])
                results.append({
                    "title": row[0], 
                    "summary": (cleaned[:350] + "...") if len(cleaned) > 350 else cleaned
                })
            conn.close()
        except Exception as e:
            logger.error(f"DB Search error: {e}")
            
        return results

    def _get_article_from_db(self, title: str) -> Optional[Dict]:
        try:
            conn = self._connect_db(read_only=True)
            cursor = conn.cursor()
            cursor.execute("SELECT title, content FROM articles WHERE title = ?", (title,))
            row = cursor.fetchone()
            conn.close()
            if row:
                content = row[1]
                if content.lower().strip().startswith("#redirect") or content.startswith("{"):
                    return None
                return {"title": row[0], "text": content}
        except Exception as e:
            logger.error(f"DB Read error: {e}")
        return None

    def _get_article_from_bz2(self, search_title: str) -> Optional[Dict]:
        if not self.file_path.exists():
            return None

        search_title = search_title.lower().strip()
        try:
            with bz2.open(self.file_path, "rt", encoding="utf-8") as f:
                context = ET.iterparse(f, events=("end",))
                for event, elem in context:
                    if elem.tag.endswith("page"):
                        title_elem = elem.find(".//title") or elem.find(f"{self.namespace}title")
                        if title_elem is not None and title_elem.text.lower() == search_title:
                            revision = elem.find(".//revision") or elem.find(f"{self.namespace}revision")
                            text_elem = revision.find(".//text") or revision.find(f"{self.namespace}text") if revision is not None else None
                            
                            if text_elem is not None and text_elem.text:
                                content = text_elem.text
                                if content.lower().strip().startswith("#redirect") or content.startswith("{"):
                                    elem.clear()
                                    continue
                                    
                                article = {
                                    "title": title_elem.text,
                                    "text": content
                                }
                                elem.clear()
                                return article
                        elem.clear()
        except Exception as e:
            logger.error(f"Error reading Wikipedia dump: {e}")
        return None

    def get_random_article_from_index(self) -> Optional[Dict]:
        """
        Returns a single random article from the index.
        Optimized to avoid full table scan using rowid.
        """
        if not self.has_index():
            return None
        
        try:
            conn = self._connect_db(read_only=True)
            cursor = conn.cursor()
            
            # Get max rowid to bound our random search
            cursor.execute("SELECT MAX(rowid) FROM articles")
            max_row = cursor.fetchone()
            max_id = max_row[0] if max_row else 0
            
            if not max_id:
                conn.close()
                return None
            
            # Try up to 20 times to find a valid article (not redirect, not special namespace)
            for _ in range(20):
                rand_id = random.randint(1, max_id)
                cursor.execute("""
                    SELECT title, content FROM articles 
                    WHERE rowid = ?
                    AND content NOT LIKE '#REDIRECT%' 
                    AND content NOT LIKE '{%'
                    AND title NOT LIKE 'Wikipedia:%'
                    AND title NOT LIKE 'Template:%'
                    AND title NOT LIKE 'Help:%'
                    AND title NOT LIKE 'Talk:%'
                    AND title NOT LIKE 'File:%'
                    AND title NOT LIKE 'Category:%'
                """, (rand_id,))
                
                row = cursor.fetchone()
                if row:
                    conn.close()
                    return {
                        "title": row[0], 
                        "text": self.clean_wikitext(row[1], preserve_links=True)
                    }
                    
            conn.close()
        except Exception as e:
            logger.error(f"Random article error: {e}")
            
        return None

    def get_random_articles(self, count: int = 5) -> List[Dict]:
        """
        Returns a few articles. Fast with DB, slow with BZ2.
        Optimized to avoid full table scan using rowid.
        """
        if self.has_index():
            try:
                conn = self._connect_db(read_only=True)
                cursor = conn.cursor()

                # Get max rowid to bound our random search
                cursor.execute("SELECT MAX(rowid) FROM articles")
                max_row = cursor.fetchone()
                max_id = max_row[0] if max_row else 0
                
                if not max_id:
                    conn.close()
                    return []

                final_featured = []
                attempts = 0
                max_attempts = count * 20  # Give plenty of tries
                
                while len(final_featured) < count and attempts < max_attempts:
                    attempts += 1
                    rand_id = random.randint(1, max_id)
                    
                    # Fetch 5000 chars and filter out redirects/JSON
                    cursor.execute("""
                        SELECT title, SUBSTR(content, 0, 5000) FROM articles 
                        WHERE rowid = ?
                        AND content NOT LIKE '#REDIRECT%' 
                        AND content NOT LIKE '{%'
                        AND title NOT LIKE 'Wikipedia:%'
                        AND title NOT LIKE 'Template:%'
                        AND title NOT LIKE 'Help:%'
                        AND title NOT LIKE 'Talk:%'
                        AND title NOT LIKE 'File:%'
                        AND title NOT LIKE 'Category:%'
                    """, (rand_id,))
                    
                    row = cursor.fetchone()
                    if row:
                        title = row[0]
                        # avoid duplicates
                        if any(f['title'] == title for f in final_featured):
                            continue
                            
                        cleaned = self.clean_wikitext(row[1])
                        # If cleaning left us with almost nothing (rare), fallback to a snippet of the title
                        summary = (cleaned[:250] + "...") if len(cleaned) > 250 else (cleaned or f"Article about {title}...")
                        final_featured.append({
                            "title": title,
                            "summary": summary
                        })
                
                conn.close()
                return final_featured
            except Exception as e:
                logger.error(f"Error fetching random articles: {e}")
                pass

        # Fallback to empty list or basic info if no index
        return []
