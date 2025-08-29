# main.py
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import base64, tempfile

import streamlit as st
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore as admin_firestore
import folium
from streamlit_folium import st_folium

# -------------------------------
# Page config
# -------------------------------
st.set_page_config(page_title="Family Live Locations", page_icon="📍", layout="wide")

# Auto-refresh every 2 minutes
try:
    from streamlit_autorefresh import st_autorefresh  # pip install streamlit-autorefresh
    st_autorefresh(interval=120_000, key="auto_refresh_2m")
except Exception:
    st.markdown(
        "<script>setTimeout(()=>location.reload(),120000);</script>",
        unsafe_allow_html=True,
    )

S = st.secrets

# -------------------------------
# Firebase Admin init (singleton)
# -------------------------------
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

@st.cache_resource(show_spinner=False)
def get_db():
    try:
        firebase_admin.get_app()
    except ValueError:
        cred = credentials.Certificate(FIREBASE_DICT)
        firebase_admin.initialize_app(cred, {"projectId": FIREBASE_DICT["project_id"]})
    return admin_firestore.client()

db = get_db()
IST = ZoneInfo("Asia/Kolkata")

def ms_to_ist(ms: int) -> str:
    if not ms:
        return "—"
    return datetime.fromtimestamp(ms/1000, tz=timezone.utc).astimezone(IST)\
        .strftime("%d %b %Y, %I:%M:%S %p IST")

# -------------------------------
# Firestore helpers
# -------------------------------
def email_to_safe_id(email: str) -> str:
    return (email.replace("@","_at_")
                 .replace(".","_dot_")
                 .replace("+","_plus_")
                 .replace("-","_dash_"))

def resolve_user_ref_by_email(email: str):
    # Try field query on "email"
    q = db.collection("users").where("email", "==", email).limit(1).stream()
    doc = next(q, None)
    if doc:
        return db.collection("users").document(doc.id)

    # Fallback to doc-id convention
    guess = db.collection("users").document(email_to_safe_id(email))
    if guess.get().exists:
        return guess
    return None

def pick_device_ref(user_ref, forced_id: str | None = None):
    devices_ref = user_ref.collection("devices")
    devices = list(devices_ref.stream())
    if not devices:
        return None

    if forced_id:
        for d in devices:
            if d.id == forced_id:
                return devices_ref.document(d.id)

    # sort by lastUpdated desc if present
    devices.sort(key=lambda d: int((d.to_dict() or {}).get("lastUpdated", 0)), reverse=True)
    return devices_ref.document(devices[0].id)

def fetch_latest_location(email: str, force_device_id: str | None = None):
    user_ref = resolve_user_ref_by_email(email)
    if not user_ref:
        return None

    device_ref = pick_device_ref(user_ref, force_device_id)
    if not device_ref:
        return None

    loc_ref = device_ref.collection("locations")
    latest = list(loc_ref.order_by("timestamp", direction=admin_firestore.Query.DESCENDING)
                        .limit(1).stream())
    if not latest:
        return None

    d = latest[0].to_dict() or {}
    try:
        lat, lng = float(d["latitude"]), float(d["longitude"])
    except Exception:
        return None

    return {
        "lat": lat,
        "lng": lng,
        "timestamp_ms": int(d.get("timestamp", 0)),
        "device_id": device_ref.id,
        "user_doc": user_ref.id,
    }

# -------------------------------
# Icon loader (Base64 / file / URL)
# -------------------------------
def icon_path_from_secrets(prefix: str) -> str | None:
    """
    Priority:
      1) {PREFIX}_ICON_BASE64    (data:... or raw base64)
      2) {PREFIX}_ICON_FILE      (local path in app)
      3) {PREFIX}_ICON_URL       (public URL)
    Returns a filesystem path (temp file) or URL usable by folium.CustomIcon.
    """
    # 1) Base64
    key_b64 = f"{prefix}_ICON_BASE64"
    if key_b64 in S and S[key_b64]:
        b64 = S[key_b64]
        if "," in b64:  # strip "data:image/..;base64,"
            b64 = b64.split(",", 1)[1]
        img = base64.b64decode(b64)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
        tmp.write(img); tmp.flush()
        return tmp.name

    # 2) File path
    key_file = f"{prefix}_ICON_FILE"
    if key_file in S and S[key_file]:
        return S[key_file]

    # 3) URL
    key_url = f"{prefix}_ICON_URL"
    if key_url in S and S[key_url]:
        return S[key_url]

    return None

# -------------------------------
# Profiles (Tom, Mom, Jerry)
# -------------------------------
PROFILES = [
    {
        "display": "Tom",
        "email": S.get("SUPER_ADMIN_EMAIL", "").strip(),
        "icon_path": icon_path_from_secrets("TOM"),
        "force_device": S.get("SUPER_ADMIN_DEVICE_ID","").strip() or None,
    },
    {
        "display": "Tom’s mom",
        "email": S.get("SUJATHA_EMAIL", "").strip(),
        "icon_path": icon_path_from_secrets("MOM"),
        "force_device": S.get("SUJATHA_DEVICE_ID","").strip() or None,
    },
    {
        "display": "Jerry",
        "email": S.get("JYOTHSNA_EMAIL", "").strip(),
        "icon_path": icon_path_from_secrets("JERRY"),
        "force_device": S.get("JYOTHSNA_DEVICE_ID","").strip() or None,
    },
]

# -------------------------------
# UI
# -------------------------------
left, right = st.columns([0.62, 0.38])
with left:
    st.markdown("## 👨‍👩‍👧 Family Live Locations")

# Fetch all latest points
results = []
with st.spinner("Contacting Firestore…"):
    for p in PROFILES:
        if not p["email"]:
            continue
        info = fetch_latest_location(p["email"], p["force_device"])
        if info:
            info["display"] = p["display"]
            info["icon_path"] = p["icon_path"]
            results.append(info)

if not results:
    st.error("No locations found. Check emails, Firestore paths, or device data.")
    st.stop()

# Right header: last updated list (IST)
with right:
    lines = []
    for r in results:
        lines.append(f"<div><b>{r['display']}</b>: {ms_to_ist(r['timestamp_ms'])}</div>")
    st.markdown(
        "<div style='text-align:right; font-size:1rem'>" +
        "<div><i>Auto-refresh: every 2 minutes</i></div>" +
        "".join(lines) +
        "</div>",
        unsafe_allow_html=True,
    )

# Build map with all markers
# Start centered on first; fit bounds to all markers
m = folium.Map(location=[results[0]["lat"], results[0]["lng"]],
               zoom_start=14, tiles="OpenStreetMap", control_scale=True)

bounds = []
for r in results:
    popup_html = (
        f"<b>{r['display']}</b><br>"
        f"Lat: {r['lat']:.6f}, Lng: {r['lng']:.6f}<br>"
        f"Updated: {ms_to_ist(r['timestamp_ms'])}<br>"
        f"Device: {r['device_id']}"
    )
    icon = None
    try:
        if r["icon_path"]:
            icon = folium.CustomIcon(r["icon_path"], icon_size=(48, 48))
    except Exception:
        icon = None

    folium.Marker(
        location=[r["lat"], r["lng"]],
        tooltip=r["display"],
        popup=popup_html,
        icon=icon if icon else folium.Icon(color="red", icon="user", prefix="fa"),
    ).add_to(m)
    bounds.append([r["lat"], r["lng"]])

if len(bounds) > 1:
    m.fit_bounds(bounds, padding=(30, 30))

st_folium(m, height=540, width=None)
st.caption("All times shown in IST · Data: Firestore · Tiles: OpenStreetMap")
