"""
🤖 COLLECTOR SCOUT - AUTO AGENT VERSION
Der Agent sucht SELBST nach Schnäppchen auf eBay!

Features:
- Automatische eBay-Suche
- Web-Scraping von Listings
- Claude Vision Bildanalyse
- Unterbewertungs-Erkennung
- Live-Datenbank mit Funden
"""

import streamlit as st
import anthropic
import requests
from bs4 import BeautifulSoup
import base64
import json
from datetime import datetime
from urllib.parse import quote
import time
import sqlite3
from pathlib import Path

# ============================================================================
# CONFIG
# ============================================================================

st.set_page_config(
    page_title="Collector Scout - Auto Agent",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# CSS
st.markdown("""
    <style>
    .agent-working {
        background: linear-gradient(135deg, #8b4513 0%, #a0522d 100%);
        color: white;
        padding: 1.5rem;
        border-radius: 12px;
        margin: 1rem 0;
    }
    
    .deal-box {
        border-left: 4px solid;
        padding: 1rem;
        border-radius: 8px;
        margin: 0.5rem 0;
    }
    
    .hot-deal {
        background: #ffebee;
        border-color: #f44336;
    }
    
    .good-deal {
        background: #fff3e0;
        border-color: #ff9800;
    }
    
    .normal {
        background: #f5f5f5;
        border-color: #999;
    }
    
    .value-display {
        font-size: 1.5rem;
        font-weight: bold;
        color: #8b4513;
    }
    </style>
""", unsafe_allow_html=True)

# ============================================================================
# DATABASE SETUP
# ============================================================================

def init_db():
    """Initialisiere Datenbank für Funde"""
    conn = sqlite3.connect('collector_findings.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            search_keyword TEXT,
            item_title TEXT,
            ebay_url TEXT,
            image_url TEXT,
            current_price REAL,
            estimated_value REAL,
            undervaluation_score INTEGER,
            analysis TEXT,
            found_at TIMESTAMP,
            notified BOOLEAN DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

def save_finding(finding: dict):
    """Speichere einen Fund in Datenbank"""
    conn = sqlite3.connect('collector_findings.db')
    c = conn.cursor()
    c.execute('''
        INSERT INTO findings 
        (search_keyword, item_title, ebay_url, image_url, current_price, 
         estimated_value, undervaluation_score, analysis, found_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        finding['keyword'],
        finding['title'],
        finding['url'],
        finding['image_url'],
        finding['price'],
        finding['estimated_value'],
        finding['score'],
        json.dumps(finding.get('analysis', {})),
        datetime.now()
    ))
    conn.commit()
    conn.close()

def get_findings(min_score: int = 60):
    """Hole alle Funde aus Datenbank"""
    conn = sqlite3.connect('collector_findings.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('''
        SELECT * FROM findings 
        WHERE undervaluation_score >= ?
        ORDER BY undervaluation_score DESC
        LIMIT 100
    ''', (min_score,))
    findings = [dict(row) for row in c.fetchall()]
    conn.close()
    return findings

# ============================================================================
# EBAY SUCHE & SCRAPING
# ============================================================================

def search_ebay(keywords: str, max_results: int = 20) -> list:
    """
    Suche auf eBay nach Keywords
    Gibt Liste von Items mit Bildern zurück
    """
    
    try:
        # eBay Search URL
        search_url = f"https://www.ebay.de/sch/i.html?_nkw={quote(keywords)}&_sop=12"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(search_url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        items = []
        listings = soup.find_all('div', {'class': 's-item'})[:max_results]
        
        for listing in listings:
            try:
                # Title
                title_elem = listing.find('h2', {'class': 's-item__title'})
                if not title_elem:
                    continue
                title = title_elem.get_text(strip=True)
                
                # URL
                link_elem = listing.find('a', {'class': 's-item__link'})
                if not link_elem:
                    continue
                url = link_elem.get('href', '')
                if not url:
                    continue
                
                # Preis
                price_elem = listing.find('span', {'class': 's-item__price'})
                if not price_elem:
                    continue
                price_text = price_elem.get_text(strip=True)
                try:
                    price = float(price_text.replace('EUR ', '').replace(',', '.').split()[0])
                except:
                    price = 0
                
                # Bild
                img_elem = listing.find('img', {'class': 's-item__image'})
                image_url = img_elem.get('src', '') if img_elem else ''
                
                if title and url and image_url:
                    items.append({
                        'title': title,
                        'url': url,
                        'price': price,
                        'image_url': image_url,
                        'keyword': keywords
                    })
            
            except Exception as e:
                continue
        
        return items
    
    except Exception as e:
        st.error(f"❌ eBay-Suche Fehler: {e}")
        return []

# ============================================================================
# BILDANALYSE MIT CLAUDE VISION
# ============================================================================

def analyze_image_url(image_url: str, item_title: str, price: float) -> dict:
    """
    Lade Bild herunter und analysiere mit Claude Vision
    Gibt Wertschätzung und Score zurück
    """
    
    try:
        # Download Bild
        response = requests.get(image_url, timeout=5)
        if response.status_code != 200:
            return None
        
        # Zu Base64
        image_base64 = base64.b64encode(response.content).decode('utf-8')
        
        # Claude Vision Analyse
        client = anthropic.Anthropic(api_key=st.secrets.get("ANTHROPIC_API_KEY"))
        
        message = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=800,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": image_base64
                            },
                        },
                        {
                            "type": "text",
                            "text": f"""Analysiere schnell diesen eBay-Artikel.

Listing Titel: {item_title}
Aktueller Preis: {price}€

Gebe NUR JSON zurück:
{{
  "itemType": "Was ist es?",
  "condition": "Zustand (Mint/VeryGood/Good/Fair/Poor)",
  "estimatedValue": 0,
  "confidence": "hoch/mittel/niedrig",
  "isReallyUndervalued": true/false,
  "reason": "Warum (nicht) unterbewertet?"
}}"""
                        }
                    ],
                }
            ],
        )
        
        # Parse JSON
        response_text = message.content[0].text
        start = response_text.find('{')
        end = response_text.rfind('}') + 1
        
        if start == -1:
            return None
        
        analysis = json.loads(response_text[start:end])
        
        # Unter-Bewertungs-Score berechnen
        estimated = analysis.get('estimatedValue', price)
        if estimated <= 0:
            return None
        
        price_diff = estimated - price
        score = int((price_diff / estimated) * 100)
        
        # Nur wenn wirklich unterbewertet
        if analysis.get('isReallyUndervalued') and score >= 30:
            return {
                'itemType': analysis.get('itemType', 'Unknown'),
                'condition': analysis.get('condition', 'Unknown'),
                'estimatedValue': estimated,
                'confidence': analysis.get('confidence', 'mittel'),
                'reason': analysis.get('reason', ''),
                'score': max(0, min(100, score))
            }
        
        return None
    
    except Exception as e:
        return None

# ============================================================================
# UI & TABS
# ============================================================================

st.markdown("# 🤖 Collector Scout - Auto Agent")
st.markdown("_Der Agent sucht selbst nach Schnäppchen auf eBay_")

tab1, tab2, tab3 = st.tabs(["🔍 Agent starten", "📊 Funde", "⚙️ Einstellungen"])

# ============================================================================
# TAB 1: AGENT STARTEN
# ============================================================================

with tab1:
    st.subheader("🚀 Automatische eBay-Suche starten")
    
    col1, col2 = st.columns([2, 1])
    
    with col1:
        search_keywords = st.text_input(
            "Suchbegriffe (kommagetrennt)",
            value="vintage pokemon cards, rare comics, old watches",
            help="Beispiel: 'vintage toys, limited edition sneakers'"
        )
    
    with col2:
        max_items = st.number_input(
            "Max Items pro Suche",
            value=10,
            min_value=1,
            max_value=50
        )
    
    min_score_filter = st.slider(
        "Mindestens diese % Unter-Bewertung",
        min_value=20,
        max_value=100,
        value=60,
        step=10
    )
    
    st.divider()
    
    # Start Button
    if st.button("🤖 Agent STARTEN - Suche & Analysiere", use_container_width=True, type="primary"):
        
        keywords_list = [k.strip() for k in search_keywords.split(',')]
        
        with st.container():
            agent_status = st.empty()
            progress_bar = st.progress(0)
            log_container = st.empty()
            
            logs = []
            total_items = 0
            good_finds = 0
            
            for keyword_idx, keyword in enumerate(keywords_list):
                # Status Update
                agent_status.markdown(f"""
                    <div class="agent-working">
                    🤖 Agent arbeitet... <br>
                    Durchsuche: <strong>{keyword}</strong>
                    </div>
                """, unsafe_allow_html=True)
                
                logs.append(f"🔍 Suche nach '{keyword}'...")
                
                # eBay durchsuchen
                items = search_ebay(keyword, max_results=max_items)
                logs.append(f"   → Gefunden: {len(items)} Items")
                
                if not items:
                    logs.append(f"   ❌ Keine Items gefunden")
                    log_container.write("\n".join(logs))
                    continue
                
                # Jedes Item analysieren
                for item_idx, item in enumerate(items):
                    progress = ((keyword_idx + (item_idx / len(items))) / len(keywords_list))
                    progress_bar.progress(progress)
                    
                    logs.append(f"\n   📷 Analysiere: {item['title'][:50]}...")
                    
                    # Bildanalyse
                    analysis = analyze_image_url(item['image_url'], item['title'], item['price'])
                    
                    if analysis and analysis['score'] >= min_score_filter:
                        good_finds += 1
                        logs.append(f"   ✅ SCHNÄPPCHEN GEFUNDEN! Score: {analysis['score']}%")
                        
                        # In Datenbank speichern
                        finding = {
                            'keyword': keyword,
                            'title': item['title'],
                            'url': item['url'],
                            'image_url': item['image_url'],
                            'price': item['price'],
                            'estimated_value': analysis['estimatedValue'],
                            'score': analysis['score'],
                            'analysis': analysis
                        }
                        save_finding(finding)
                    else:
                        logs.append(f"   ⚪ Normaler Preis")
                    
                    total_items += 1
                    log_container.write("\n".join(logs[-5:]))  # Nur letzte 5 anzeigen
                    time.sleep(0.5)  # Rate limiting
            
            # Fertig
            progress_bar.progress(1.0)
            agent_status.markdown(f"""
                <div class="agent-working">
                ✅ Agent fertig! <br>
                {total_items} Items analysiert | {good_finds} Schnäppchen gefunden 🎉
                </div>
            """, unsafe_allow_html=True)
            
            st.success(f"✅ Suche abgeschlossen! {good_finds} gute Chancen gefunden!")
            st.info("📊 Schau den Tab 'Funde' für alle Ergebnisse!")

# ============================================================================
# TAB 2: FUNDE ANZEIGEN
# ============================================================================

with tab2:
    st.subheader("📊 Gefundene Schnäppchen")
    
    findings = get_findings(min_score=30)
    
    if not findings:
        st.info("Noch keine Funde. Starte den Agent im Tab 'Agent starten'!")
    else:
        st.write(f"**{len(findings)} Schnäppchen gefunden**")
        
        # Filter
        min_score = st.slider(
            "Nur Items mit mind. dieser % Unter-Bewertung anzeigen",
            min_value=30,
            max_value=100,
            value=60
        )
        
        filtered_findings = [f for f in findings if f['undervaluation_score'] >= min_score]
        
        st.divider()
        
        for finding in filtered_findings:
            score = finding['undervaluation_score']
            price = finding['current_price']
            value = finding['estimated_value']
            
            # Farbe basierend auf Score
            if score >= 75:
                css_class = 'hot-deal'
                badge = '🔥 HOT DEAL'
            elif score >= 60:
                css_class = 'good-deal'
                badge = '⚡ GUTES ANGEBOT'
            else:
                css_class = 'normal'
                badge = '✓ ANGEBOT'
            
            col1, col2 = st.columns([3, 1])
            
            with col1:
                st.markdown(f"""
                    <div class="deal-box {css_class}">
                    <strong>{finding['item_title'][:60]}</strong><br>
                    💰 Preis: <span class="value-display">{price:.2f}€</span> → 
                    Wert: {value:.2f}€ ({score}% Schnäppchen)<br>
                    🏷️ {badge}<br>
                    <a href="{finding['ebay_url']}" target="_blank">→ Zum Angebot auf eBay</a>
                    </div>
                """, unsafe_allow_html=True)
            
            with col2:
                analysis = json.loads(finding['analysis']) if finding['analysis'] else {}
                st.write(f"**Zustand:** {analysis.get('condition', '?')}")
                st.write(f"**Konfidenz:** {analysis.get('confidence', '?')}")

# ============================================================================
# TAB 3: EINSTELLUNGEN
# ============================================================================

with tab3:
    st.subheader("⚙️ Einstellungen")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.write("### API Konfiguration")
        if not st.secrets.get("ANTHROPIC_API_KEY"):
            st.error("⚠️ Claude API Key nicht konfiguriert!")
            st.info("""
            Für Streamlit Cloud:
            1. Gehe zu Settings → Secrets
            2. Paste: `ANTHROPIC_API_KEY = "sk-..."`
            3. Save
            """)
        else:
            st.success("✅ Claude API Key konfiguriert")
    
    with col2:
        st.write("### Datenbank")
        st.metric("Gespeicherte Funde", len(get_findings(min_score=0)))
        
        if st.button("🗑️ Datenbank löschen", help="Alle Funde löschen"):
            import os
            if os.path.exists('collector_findings.db'):
                os.remove('collector_findings.db')
                init_db()
                st.success("Datenbank gelöscht!")
                st.rerun()
    
    st.divider()
    
    st.write("### Info")
    st.write("""
    **Wie funktioniert der Agent?**
    
    1. **Suche:** Agent sucht auf eBay nach deinen Keywords
    2. **Download:** Bilder von gefundenen Items werden heruntergeladen
    3. **Analyse:** Claude Vision analysiert jedes Bild
    4. **Bewertung:** Berechnet wie unterbewertet der Preis ist
    5. **Speichern:** Gute Funde werden in der Datenbank gespeichert
    6. **Anzeigen:** Du siehst alle Funde in einem Tab
    
    **Kosten:**
    - ~€0.003 pro analysiertem Bild
    - Bei 50 Items: ~€0.15
    
    **Wichtig:**
    - Nur für deine eigene Nutzung!
    - eBay TOS beachten (kein Spam)
    - Kaufentscheidungen selbst treffen
    """)

# ============================================================================
# FOOTER
# ============================================================================

st.divider()
st.caption("🤖 Collector Scout Auto Agent | Powered by Claude Vision & Streamlit Cloud")

# Init DB on startup
init_db()
