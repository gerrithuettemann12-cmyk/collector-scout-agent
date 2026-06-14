import streamlit as st
import anthropic
import requests
from bs4 import BeautifulSoup
import base64
import json
from datetime import datetime
from urllib.parse import quote
import time
import csv
from io import StringIO

st.set_page_config(
    page_title="Collector Scout - Auto Agent",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

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

# Session State initialisieren (KEINE DATENBANK!)
if 'findings' not in st.session_state:
    st.session_state.findings = []
if 'search_history' not in st.session_state:
    st.session_state.search_history = []

def search_ebay(keywords: str, max_results: int = 20) -> list:
    try:
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
                title_elem = listing.find('h2', {'class': 's-item__title'})
                if not title_elem:
                    continue
                title = title_elem.get_text(strip=True)
                link_elem = listing.find('a', {'class': 's-item__link'})
                if not link_elem:
                    continue
                url = link_elem.get('href', '')
                if not url:
                    continue
                price_elem = listing.find('span', {'class': 's-item__price'})
                if not price_elem:
                    continue
                price_text = price_elem.get_text(strip=True)
                try:
                    price = float(price_text.replace('EUR ', '').replace(',', '.').split()[0])
                except:
                    price = 0
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
            except:
                continue
        return items
    except Exception as e:
        st.error(f"❌ eBay-Suche Fehler: {e}")
        return []

def analyze_image_url(image_url: str, item_title: str, price: float) -> dict:
    try:
        response = requests.get(image_url, timeout=5)
        if response.status_code != 200:
            return None
        image_base64 = base64.b64encode(response.content).decode('utf-8')
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
{{"itemType": "Was ist es?", "condition": "Zustand (Mint/VeryGood/Good/Fair/Poor)", "estimatedValue": 0, "confidence": "hoch/mittel/niedrig", "isReallyUndervalued": true/false, "reason": "Warum (nicht) unterbewertet?"}}"""
                        }
                    ],
                }
            ],
        )
        response_text = message.content[0].text
        start = response_text.find('{')
        end = response_text.rfind('}') + 1
        if start == -1:
            return None
        analysis = json.loads(response_text[start:end])
        estimated = analysis.get('estimatedValue', price)
        if estimated <= 0:
            return None
        price_diff = estimated - price
        score = int((price_diff / estimated) * 100)
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
    except:
        return None

st.markdown("# 🤖 Collector Scout - Auto Agent")
st.markdown("_Der Agent sucht selbst nach Schnäppchen auf eBay_")

tab1, tab2, tab3 = st.tabs(["🔍 Agent starten", "📊 Funde", "⚙️ Einstellungen"])

with tab1:
    st.subheader("🚀 Automatische eBay-Suche starten")
    col1, col2 = st.columns([2, 1])
    with col1:
        search_keywords = st.text_input(
            "Suchbegriffe (kommagetrennt)",
            value="vintage pokemon cards, rare comics, old watches",
        )
    with col2:
        max_items = st.number_input(
            "Max Items pro Suche",
            value=5,
            min_value=1,
            max_value=20
        )
    min_score_filter = st.slider(
        "Mindestens diese % Unter-Bewertung",
        min_value=20,
        max_value=100,
        value=60,
        step=10
    )
    st.divider()
    
    if st.button("🤖 Agent STARTEN", use_container_width=True, type="primary"):
        keywords_list = [k.strip() for k in search_keywords.split(',')]
        agent_status = st.empty()
        progress_bar = st.progress(0)
        log_container = st.empty()
        logs = []
        total_items = 0
        good_finds = 0
        
        for keyword_idx, keyword in enumerate(keywords_list):
            agent_status.markdown(f"""
                <div class="agent-working">
                🤖 Agent arbeitet... <br>
                Durchsuche: <strong>{keyword}</strong>
                </div>
            """, unsafe_allow_html=True)
            logs.append(f"🔍 Suche nach '{keyword}'...")
            items = search_ebay(keyword, max_results=max_items)
            logs.append(f"   → Gefunden: {len(items)} Items")
            
            if not items:
                logs.append(f"   ❌ Keine Items gefunden")
                log_container.write("\n".join(logs))
                continue
            
            for item_idx, item in enumerate(items):
                progress = ((keyword_idx + (item_idx / len(items))) / len(keywords_list))
                progress_bar.progress(progress)
                logs.append(f"\n   📷 Analysiere: {item['title'][:50]}...")
                analysis = analyze_image_url(item['image_url'], item['title'], item['price'])
                
                if analysis and analysis['score'] >= min_score_filter:
                    good_finds += 1
                    logs.append(f"   ✅ SCHNÄPPCHEN! Score: {analysis['score']}%")
                    finding = {
                        'keyword': keyword,
                        'title': item['title'],
                        'url': item['url'],
                        'image_url': item['image_url'],
                        'price': item['price'],
                        'estimated_value': analysis['estimatedValue'],
                        'score': analysis['score'],
                        'analysis': analysis,
                        'found_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                    st.session_state.findings.append(finding)
                else:
                    logs.append(f"   ⚪ Normaler Preis")
                total_items += 1
                log_container.write("\n".join(logs[-5:]))
                time.sleep(0.5)
        
        progress_bar.progress(1.0)
        agent_status.markdown(f"""
            <div class="agent-working">
            ✅ Agent fertig! <br>
            {total_items} Items analysiert | {good_finds} Schnäppchen gefunden 🎉
            </div>
        """, unsafe_allow_html=True)
        st.success(f"✅ Suche abgeschlossen! {good_finds} gute Chancen gefunden!")
        st.session_state.search_history.append({
            'keywords': search_keywords,
            'items_found': total_items,
            'deals_found': good_finds,
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })

with tab2:
    st.subheader("📊 Gefundene Schnäppchen")
    
    if not st.session_state.findings:
        st.info("🎯 Noch keine Funde! Starte den Agent im Tab '🔍 Agent starten'")
    else:
        st.write(f"**{len(st.session_state.findings)} Schnäppchen gefunden**")
        
        col1, col2, col3 = st.columns(3)
        with col1:
            min_score = st.slider(
                "Filter nach Score",
                min_value=30,
                max_value=100,
                value=60
            )
        with col2:
            if st.button("🗑️ Alle löschen"):
                st.session_state.findings = []
                st.rerun()
        with col3:
            if st.button("📥 Als CSV exportieren"):
                csv_data = StringIO()
                if st.session_state.findings:
                    writer = csv.DictWriter(csv_data, fieldnames=['title', 'price', 'estimated_value', 'score', 'url', 'found_at'])
                    writer.writeheader()
                    for f in st.session_state.findings:
                        writer.writerow({
                            'title': f['title'],
                            'price': f['price'],
                            'estimated_value': f['estimated_value'],
                            'score': f['score'],
                            'url': f['url'],
                            'found_at': f.get('found_at', '')
                        })
                    st.download_button(
                        label="📊 Download CSV",
                        data=csv_data.getvalue(),
                        file_name="collector_scout_findings.csv",
                        mime="text/csv"
                    )
        
        st.divider()
        
        filtered_findings = [f for f in st.session_state.findings if f['score'] >= min_score]
        
        for idx, finding in enumerate(filtered_findings):
            score = finding['score']
            price = finding['price']
            value = finding['estimated_value']
            
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
                    <strong>{finding['title'][:70]}</strong><br>
                    💰 Preis: <span class="value-display">{price:.2f}€</span> → 
                    Wert: {value:.2f}€ ({score}% Schnäppchen)<br>
                    🏷️ {badge} | 📅 {finding.get('found_at', 'N/A')}<br>
                    <a href="{finding['url']}" target="_blank">→ Zum Angebot auf eBay</a>
                    </div>
                """, unsafe_allow_html=True)
            
            with col2:
                analysis = finding.get('analysis', {})
                st.write(f"**{analysis.get('condition', '?')}**")
                st.write(f"*{analysis.get('confidence', '?')}*")

with tab3:
    st.subheader("⚙️ Einstellungen & Info")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.write("### API Status")
        if not st.secrets.get("ANTHROPIC_API_KEY"):
            st.error("❌ Claude API Key nicht konfiguriert!")
        else:
            st.success("✅ Claude API Key aktiv")
    
    with col2:
        st.write("### Statistiken")
        st.metric("Gefundene Schnäppchen", len(st.session_state.findings))
        st.metric("Suchanfragen", len(st.session_state.search_history))
    
    st.divider()
    
    st.write("### 📚 Suchverlauf")
    if st.session_state.search_history:
        for search in st.session_state.search_history[-5:]:
            st.write(f"🔍 {search['keywords']} - {search['deals_found']} Deals gefunden - {search['timestamp']}")
    else:
        st.info("Noch keine Suchen durchgeführt")
    
    st.divider()
    
    st.write("### ℹ️ About")
    st.write("""
    **Collector Scout - Auto Agent**
    
    Ein intelligenter eBay-Agent der:
    - 🔍 Automatisch nach Items sucht
    - 🧠 Mit Claude Vision Bilder analysiert
    - 💰 Den wahren Wert schätzt
    - 📊 Unterbewertete Schnäppchen findet
    
    **Features:**
    - ✅ Live-Suche mit Fortschrittsanzeige
    - ✅ Claude Vision Bildanalyse
    - ✅ CSV-Export
    - ✅ Suchverlauf
    - ✅ Filterung nach Score
    
    **Kosten:** ~€0.003 pro analysiertem Bild
    
    **Made with ❤️ by Collector Scout Team**
    """)

st.divider()
st.caption("🤖 Collector Scout Auto Agent | Powered by Claude Vision & Streamlit Cloud")
