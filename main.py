import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import streamlit as st
from firebase_admin import credentials, initialize_app
from google.cloud import firestore
from streamlit_folium import st_folium
import folium

# -------------------------------
# Page config (mobile + desktop)
# -------------------------------
st.set_page_config(
    page_title="My Live Location",
    page_icon="🗺️",
    layout="wide",
)

# -------------------------------
# Auto-refresh every 2 minutes
# -------------------------------
# Prefer built-in autorefresh helper from streamlit >=1.31
try:
    from streamlit_autorefresh import st_autorefresh  # pip: streamlit-autorefresh
    st_autorefresh(interval=120_000, key="auto_refresh_2min")
except Exception:
    # Fallback: a tiny JS-based refresh (works on most installs)
    st.markdown(
        """
        <script>
        setTimeout(function(){ window.location.reload(); }, 120000);
        </script>
        """,
        unsafe_allow_html=True,
    )

# -------------------------------
# Secrets / configuration
# -------------------------------
SECRETS = st.secrets

# Firebase Admin dict (taken from .streamlit/secrets.toml)
fb = {
    "type": SECRETS["FIREBASE_TYPE"],
    "project_id": SECRETS["FIREBASE_PROJECT_ID"],
    "private_key_id": SECRETS["FIREBASE_PRIVATE_KEY_ID"],
    "private_key": SECRETS["FIREBASE_PRIVATE_KEY"].replace("\\n", "\n"),
    "client_email": SECRETS["FIREBASE_CLIENT_EMAIL"],
    "client_id": SECRETS["FIREBASE_CLIENT_ID"],
    "auth_uri": SECRETS["FIREBASE_AUTH_URI"],
    "token_uri": SECRETS["FIREBASE_TOKEN_URI"],
    "auth_provider_x509_cert_url": SECRETS["FIREBASE_AUTH_PROVIDER_X509_CERT_URL"],
    "client_x509_cert_url": SECRETS["FIREBASE_CLIENT_X509_CERT_URL"],
    "universe_domain": SECRETS["FIREBASE_UNIVERSE_DOMAIN"],
}

SUPER_ADMIN_EMAIL = SECRETS.get("SUPER_ADMIN_EMAIL", "")
TOM_ICON_URL = SECRETS.get("TOM_ICON_URL", "")  # optional custom marker image (png)

# -------------------------------
# Firebase init (singleton)
# -------------------------------
@st.cache_resource(show_spinner=False)
def get_firestore_client():
    if not initialize_app._apps:  # avoid re-init in some environments
        cred = credentials.Certificate(fb)
        initialize_app(cred, {"projectId": fb["project_id"]})
    return firestore.Client(project=fb["project_id"])

db = get_firestore_client()

# -------------------------------
# Helpers
# -------------------------------
IST = ZoneInfo("Asia/Kolkata")

def fmt_ist(ms_epoch: int) -> str:
    """Convert epoch milliseconds to IST pretty string."""
    dt = datetime.fromtimestamp(ms_epoch / 1000, tz=timezone.utc).astimezone(IST)
    return dt.strftime("%d %b %Y, %I:%M:%S %p IST")

def get_latest_location_for_email(email: str):
    """
    Resolve the user doc by email, then find a device and fetch the newest
    document from its 'locations' subcollection.
    Returns dict: {lat, lng, timestamp_ms, device_id} or None.
    """
    # 1) Find user doc by email (field)
    users_ref = db.collection("users").where("email", "==", email).limit(1)
    snap = users_ref.stream()
    user_doc = next(snap, None)
    if not user_doc:
        return None

    user_ref = db.collection("users").document(user_doc.id)

    # 2) Choose device: newest by a metadata field if available; else just first
    devices_ref = user_ref.collection("devices")
    devices = list(devices_ref.stream())
    if not devices:
        return None

    # Try to choose the device with the most recent 'lastUpdated' or else first
    def device_last_updated(doc):
        data = doc.to_dict() or {}
        return int(data.get("lastUpdated", 0))  # epoch ms if present

    devices.sort(key=device_last_updated, reverse=True)
    chosen_device = devices[0]
    device_id = chosen_device.id

    # 3) Latest location (order by 'timestamp' desc)
    loc_ref = user_ref.collection("devices").document(device_id).collection("locations")
    latest_q = loc_ref.order_by("timestamp", direction=firestore.Query.DESCENDING).limit(1)
    latest = list(latest_q.stream())
    if not latest:
        return None

    loc = latest[0].to_dict()
    return {
        "lat": float(loc["latitude"]),
        "lng": float(loc["longitude"]),
        "timestamp_ms": int(loc.get("timestamp", 0)),
        "device_id": device_id,
    }

# -------------------------------
# UI
# -------------------------------
title_left, time_right = st.columns([0.65, 0.35])
with title_left:
    st.markdown("## 🧭 My Live Location")

with time_right:
    st.markdown(
        """
        <div style="text-align:right; font-size:0.95rem; opacity:0.85;">
            Auto-refresh: every 2 minutes
        </div>
        """,
        unsafe_allow_html=True,
    )

with st.spinner("Fetching latest location from Firestore…"):
    info = get_latest_location_for_email(SUPER_ADMIN_EMAIL)

if not info:
    st.error("Couldn't find a location record. Please verify Firestore data and email in secrets.")
    st.stop()

# Last updated (IST)
last_updated_str = fmt_ist(info["timestamp_ms"])
right_col = st.columns([0.6, 0.4])[1]
with right_col:
    st.markdown(
        f"""
        <div style="text-align:right; font-size:1rem;">
            <strong>Last updated:</strong> {last_updated_str}
        </div>
        """,
        unsafe_allow_html=True,
    )

# -------------------------------
# Map (Folium + OpenStreetMap)
# -------------------------------
lat, lng = info["lat"], info["lng"]

m = folium.Map(location=[lat, lng], zoom_start=16, tiles="OpenStreetMap", control_scale=True)

# Tom icon if provided, else a clean default marker
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

# Show map (fits both mobile & desktop nicely)
st_folium(m, height=520, width=None)
st.caption(f"Device: {info['device_id']}")

# Minimal footer (mobile-friendly)
st.markdown(
    "<div style='text-align:center; opacity:0.6; font-size:0.9rem;'>Data source: Firestore · Tiles: OpenStreetMap</div>",
    unsafe_allow_html=True,
)
