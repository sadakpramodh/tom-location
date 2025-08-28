# main.py
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import streamlit as st
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore as admin_firestore

from streamlit_folium import st_folium
import folium

# -------------------------------
# Page config (mobile & desktop)
# -------------------------------
st.set_page_config(
    page_title="My Live Location",
    page_icon="🗺️",
    layout="wide",
)

# -------------------------------
# Auto-refresh every 2 minutes
# -------------------------------
# Try the helper package; if missing, fall back to a JS refresh.
try:
    from streamlit_autorefresh import st_autorefresh  # pip install streamlit-autorefresh
    st_autorefresh(interval=120_000, key="auto_refresh_2min")
except Exception:
    st.markdown(
        """
        <script>
          setTimeout(function(){ location.reload(); }, 120000);
        </script>
        """,
        unsafe_allow_html=True,
    )

# -------------------------------
# Secrets / Configuration
# -------------------------------
S = st.secrets
FIREBASE_DICT = {
    "type": S["FIREBASE_TYPE"],
    "project_id": S["FIREBASE_PROJECT_ID"],
    "private_key_id": S["FIREBASE_PRIVATE_KEY_ID"],
    "private_key": S["FIREBASE_PRIVATE_KEY"].replace("\\n", "\n"),
    "client_email": S["FIREBASE_CLIENT_EMAIL"],
    "client_id": S["FIREBASE_CLIENT_ID"],
    "auth_uri": S["FIREBASE_AUTH_URI"],
    "token_uri": S["FIREBASE_TOKEN_URI"],
    "auth_provider_x509_cert_url": S["FIREBASE_AUTH_PROVIDER_X509_CERT_URL"],
    "client_x509_cert_url": S["FIREBASE_CLIENT_X509_CERT_URL"],
    "universe_domain": S.get("FIREBASE_UNIVERSE_DOMAIN", "googleapis.com"),
}

# Who to fetch
USER_EMAIL = S.get("SUPER_ADMIN_EMAIL", "").strip()

# Optional overrides / visuals
FORCE_DEVICE_ID = S.get("FORCE_DEVICE_ID", "").strip()         # if you want to pin a device
TOM_ICON_URL   = S.get("TOM_ICON_URL", "").strip()             # PNG for Tom marker (48x48 works well)

# -------------------------------
# Firebase Admin init (singleton)
# -------------------------------
@st.cache_resource(show_spinner=False)
def get_firestore_client():
    # Initialize once, safely across Streamlit reruns
    try:
        firebase_admin.get_app()
    except ValueError:
        cred = credentials.Certificate(FIREBASE_DICT)
        firebase_admin.initialize_app(cred, {"projectId": FIREBASE_DICT["project_id"]})
    return admin_firestore.client()

db = get_firestore_client()

# -------------------------------
# Time helpers
# -------------------------------
IST = ZoneInfo("Asia/Kolkata")

def ms_to_ist_str(ms: int) -> str:
    """Epoch ms -> 'DD Mon YYYY, HH:MM:SS AM/PM IST'"""
    if not ms:
        return "—"
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(IST)
    return dt.strftime("%d %b %Y, %I:%M:%S %p IST")

# -------------------------------
# Firestore fetch helpers
# -------------------------------
def email_to_safe_id(email: str) -> str:
    """Fallback: build doc id like 'name_at_domain_dot_com' if needed."""
    return (
        email.replace("@", "_at_")
        .replace(".", "_dot_")
        .replace("+", "_plus_")
        .replace("-", "_dash_")
    )

def resolve_user_doc_by_email(email: str):
    """
    Prefer a field query on 'email'. If missing, fall back to docId pattern.
    Returns (user_doc_id, user_ref) or (None, None).
    """
    # Try field query
    q = db.collection("users").where("email", "==", email).limit(1).stream()
    doc = next(q, None)
    if doc:
        return doc.id, db.collection("users").document(doc.id)

    # Fallback to doc id convention
    guess_id = email_to_safe_id(email)
    guess_ref = db.collection("users").document(guess_id)
    snap = guess_ref.get()
    if snap.exists:
        return guess_id, guess_ref

    return None, None

def pick_device_ref(user_ref):
    """
    Choose a device document for the user.
    Priority:
      1) FORCE_DEVICE_ID (if provided and exists)
      2) Device with highest 'lastUpdated' field
      3) Otherwise, first device found
    Returns (device_id, device_ref) or (None, None)
    """
    devices_ref = user_ref.collection("devices")
    devices = list(devices_ref.stream())
    if not devices:
        return None, None

    if FORCE_DEVICE_ID:
        for d in devices:
            if d.id == FORCE_DEVICE_ID:
                return d.id, devices_ref.document(d.id)

    # Sort by 'lastUpdated' desc if present
    def last_updated(doc):
        data = doc.to_dict() or {}
        return int(data.get("lastUpdated", 0))

    devices.sort(key=last_updated, reverse=True)
    chosen = devices[0]
    return chosen.id, devices_ref.document(chosen.id)

def fetch_latest_location(user_email: str):
    """
    Resolve user -> device -> latest location by 'timestamp' DESC.
    Returns dict {lat, lng, timestamp_ms, device_id} or None.
    """
    user_id, user_ref = resolve_user_doc_by_email(user_email)
    if not user_ref:
        return None

    device_id, device_ref = pick_device_ref(user_ref)
    if not device_ref:
        return None

    loc_ref = device_ref.collection("locations")
    latest = list(
        loc_ref.order_by("timestamp", direction=admin_firestore.Query.DESCENDING).limit(1).stream()
    )
    if not latest:
        return None

    loc = latest[0].to_dict() or {}
    try:
        lat = float(loc["latitude"])
        lng = float(loc["longitude"])
    except Exception:
        return None

    return {
        "lat": lat,
        "lng": lng,
        "timestamp_ms": int(loc.get("timestamp", 0)),
        "device_id": device_id,
    }

# -------------------------------
# UI
# -------------------------------
header_left, header_right = st.columns([0.66, 0.34])

with header_left:
    st.markdown("## 🧭 My Live Location")

with header_right:
    st.markdown(
        "<div style='text-align:right; opacity:0.85;'>Auto-refresh: every 2 minutes</div>",
        unsafe_allow_html=True,
    )

if not USER_EMAIL:
    st.error("SUPER_ADMIN_EMAIL is missing in secrets.")
    st.stop()

with st.spinner("Fetching latest location from Firestore…"):
    latest = fetch_latest_location(USER_EMAIL)

if not latest:
    st.error("No location found. Check Firestore paths/fields and your email in secrets.")
    st.stop()

# Last updated (IST) in the top-right
last_updated = ms_to_ist_str(latest["timestamp_ms"])
st.markdown(
    f"""
    <div style="text-align:right; font-size:1rem; margin-top:-0.5rem;">
      <strong>Last updated:</strong> {last_updated}
    </div>
    """,
    unsafe_allow_html=True,
)

# -------------------------------
# Map rendering
# -------------------------------
lat, lng = latest["lat"], latest["lng"]

m = folium.Map(
    location=[lat, lng],
    zoom_start=16,
    tiles="OpenStreetMap",
    control_scale=True,
)

# Tom icon if provided; else a standard user marker
try:
    if TOM_ICON_URL:
        icon = folium.CustomIcon(TOM_ICON_URL, icon_size=(48, 48))
        folium.Marker([lat, lng], icon=icon, tooltip="You are here").add_to(m)
    else:
        folium.Marker(
            [lat, lng],
            tooltip="You are here",
            icon=folium.Icon(color="red", icon="user", prefix="fa"),
        ).add_to(m)
except Exception:
    folium.Marker([lat, lng], tooltip="You are here").add_to(m)

st_folium(m, height=520, width=None)
st.caption(f"Device: {latest['device_id']}")

st.markdown(
    "<div style='text-align:center; opacity:0.6; font-size:0.9rem;'>"
    "Data source: Firestore · Tiles: OpenStreetMap"
    "</div>",
    unsafe_allow_html=True,
)
