from datetime import datetime
import hashlib
import io
import secrets
import extra_streamlit_components as stx
import pandas as pd
from PIL import Image
import streamlit as st
from sqlalchemy import text

# ---------------- BASELINE INITIALIZATION ---------------- #
if "logged_in" not in st.session_state: st.session_state.logged_in = False
if "username" not in st.session_state: st.session_state.username = None
if "user_id" not in st.session_state: st.session_state.user_id = None
if "editing_book_id" not in st.session_state: st.session_state.editing_book_id = None
if "library_config" not in st.session_state: st.session_state.library_config = None
if "account_vault" not in st.session_state: st.session_state.account_vault = {}
if "adding_new_account" not in st.session_state: st.session_state.adding_new_account = False

conn = st.connection("postgresql", type="sql")
try: cookie_manager = stx.CookieManager()
except: cookie_manager = None

DEFAULT_CATEGORIES = ["Read pending", "Reading in progress", "Already read", "Read again", "Give away", "Wishlist"]

def compute_categories(mode, raw_custom):
    custom_list = [c.strip() for c in raw_custom.split(",") if c.strip()] if raw_custom else []
    if mode == "Custom Only" and custom_list: return custom_list
    elif mode == "Default + Custom": return DEFAULT_CATEGORIES + custom_list
    return DEFAULT_CATEGORIES

# ---------------- ACTIVE KICK-OUT GUARD ---------------- #
if st.session_state.logged_in and st.session_state.library_config is not None:
    active_code = st.session_state.library_config.get("access_code")
    check_active = conn.query("SELECT library_name, library_type, max_accounts, custom_categories, category_mode FROM library_configurations WHERE access_code=:ac", params={"ac": active_code}, ttl=0)
    if check_active.empty:
        st.session_state.library_config = None
        if cookie_manager:
            try: cookie_manager.delete(cookie="library_access_code")
            except: pass
        st.warning("⚠️ The active session configuration access code was deleted by an administrator.")
    else:
        st.session_state.library_config.update({
            "name": check_active.iloc[0]["library_name"],
            "type": check_active.iloc[0]["library_type"],
            "max_accounts": int(check_active.iloc[0]["max_accounts"]),
            "categories": compute_categories(check_active.iloc[0]["category_mode"], check_active.iloc[0]["custom_categories"])
        })

dynamic_title, dynamic_icon = "Book Library", "📚"
if st.session_state.library_config is not None:
    dynamic_title, dynamic_icon = f"{st.session_state.library_config['name']} Tracker", "📖"
elif st.session_state.username == "admin":
    dynamic_title, dynamic_icon = "Admin Library Panel", "👑"

st.set_page_config(page_title=dynamic_title, page_icon=dynamic_icon, layout="wide")

# ---------------- DATABASE SETUP (OPTIMIZED) ---------------- #
def make_hashes(password): return hashlib.sha256(str.encode(password)).hexdigest()

def init_db():
    with conn.session as s:
        s.execute(text("CREATE TABLE IF NOT EXISTS users (id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, registration_date TEXT NOT NULL)"))
        s.execute(text("CREATE TABLE IF NOT EXISTS library_configurations (id SERIAL PRIMARY KEY, library_name TEXT NOT NULL, access_code TEXT UNIQUE NOT NULL, library_type TEXT NOT NULL DEFAULT 'Singular', max_accounts INTEGER NOT NULL DEFAULT 1, custom_categories TEXT, created_at TEXT NOT NULL)"))
        s.commit()
    try:
        with conn.session as s:
            s.execute(text("ALTER TABLE library_configurations ADD COLUMN IF NOT EXISTS category_mode TEXT DEFAULT 'Default Only'"))
            s.commit()
    except: pass
    with conn.session as s:
        s.execute(text("CREATE TABLE IF NOT EXISTS library_memberships (id SERIAL PRIMARY KEY, config_id INTEGER NOT NULL, user_id INTEGER NOT NULL, joined_at TEXT NOT NULL, is_leader BOOLEAN DEFAULT FALSE, UNIQUE (config_id, user_id), FOREIGN KEY (config_id) REFERENCES library_configurations(id) ON DELETE CASCADE, FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)"))
        s.execute(text("CREATE TABLE IF NOT EXISTS books (id SERIAL PRIMARY KEY, config_id INTEGER NOT NULL, user_id INTEGER NOT NULL, title TEXT NOT NULL, category TEXT NOT NULL, image_bytes BYTEA, image_name TEXT, FOREIGN KEY (config_id) REFERENCES library_configurations(id) ON DELETE CASCADE, FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)"))
        s.execute(text("CREATE TABLE IF NOT EXISTS user_sessions (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, username TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)"))
        s.commit()

if "db_initialized" not in st.session_state:
    init_db()
    st.session_state.db_initialized = True

# ---------------- MULTI-VAULT COOKIES ---------------- #
if not st.session_state.logged_in and not st.session_state.adding_new_account and cookie_manager:
    try:
        vault_cookie = cookie_manager.get(cookie="library_vault_tokens")
        if vault_cookie:
            for t in vault_cookie.split(","):
                if not t.strip(): continue
                token_check = conn.query("SELECT user_id, username FROM user_sessions WHERE token=:t", params={"t": t.strip()}, ttl=0)
                if not token_check.empty:
                    st.session_state.account_vault[token_check.iloc[0]["username"]] = int(token_check.iloc[0]["user_id"])
            if st.session_state.account_vault:
                st.session_state.logged_in = True
                first_user = list(st.session_state.account_vault.keys())[0]
                st.session_state.username = first_user
                st.session_state.user_id = st.session_state.account_vault[first_user]
    except: pass

if st.session_state.logged_in and st.session_state.library_config is None and cookie_manager:
    try:
        saved_code = cookie_manager.get(cookie="library_access_code")
        if saved_code:
            match_df = conn.query("SELECT id, library_name, library_type, max_accounts, custom_categories, category_mode FROM library_configurations WHERE access_code=:ac", params={"ac": saved_code}, ttl=0)
            if not match_df.empty:
                st.session_state.library_config = {
                    "name": match_df.iloc[0]["library_name"],
                    "access_code": saved_code,
                    "type": match_df.iloc[0]["library_type"],
                    "max_accounts": int(match_df.iloc[0]["max_accounts"]),
                    "categories": compute_categories(match_df.iloc[0]["category_mode"], match_df.iloc[0]["custom_categories"])
                }
    except: pass

st.query_params.clear()

# ---------------- AUTHENTICATION & VAULT UI ---------------- #
if not st.session_state.logged_in:
    st.title("📚 Book Library")
    st.subheader("Add an Account to your Vault" if st.session_state.adding_new_account else "Please Login or Register")
        
    auth_mode = st.radio("Choose Action", ["Login", "Register"], horizontal=True)
    
    username = st.text_input("Username").strip()
    password = st.text_input("Password", type="password")
    
    if auth_mode == "Register":
        if st.button("Create Account", type="primary"):
            if not username or not password: st.error("Please fill in all fields.")
            else:
                try:
                    with conn.session as s:
                        s.execute(text("INSERT INTO users (username, password, registration_date) VALUES (:u, :p, :r)"), {"u": username, "p": make_hashes(password), "r": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                        s.commit()
                    st.success("Registration successful! Switch to Login.")
                except: st.error("Username already taken or network issue.")
    else: # Login
        remember_me = st.checkbox("Keep me logged in")
        if st.button("Login", type="primary"):
            if not username or not password: st.error("Please fill in all fields.")
            else:
                df = conn.query("SELECT id, username FROM users WHERE username=:u AND password=:p", params={"u": username, "p": make_hashes(password)}, ttl=0)
                if not df.empty:
                    uid = int(df.iloc[0]["id"])
                    st.session_state.update({"adding_new_account": False, "logged_in": True, "user_id": uid, "username": username})
                    st.session_state.account_vault[username] = uid
                    
                    if remember_me and cookie_manager:
                        token = secrets.token_urlsafe(32)
                        with conn.session as s:
                            s.execute(text("INSERT INTO user_sessions (token, user_id, username, created_at) VALUES (:t, :uid, :u, :c)"), {"t": token, "uid": uid, "u": username, "c": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                            s.commit()
                        existing_cookie = cookie_manager.get(cookie="library_vault_tokens")
                        new_cookie = f"{existing_cookie},{token}" if existing_cookie else token
                        try: cookie_manager.set(cookie="library_vault_tokens", val=new_cookie, expires_at=datetime.now() + pd.Timedelta(days=30))
                        except: pass
                    st.rerun()
                else: st.error("Invalid username or password.")
                    
    if st.session_state.adding_new_account and st.button("Cancel & Return to Vault", use_container_width=True):
        st.session_state.update({"adding_new_account": False, "logged_in": True})
        st.rerun()
    st.stop()

is_admin = st.session_state.username.lower() == "admin"

# ---------------- GLOBAL MANAGEMENT SIDEBAR ---------------- #
with st.sidebar:
    st.header("Control Panel")
    st.success(f"Active: **{st.session_state.username}**" + (" *(Admin)*" if is_admin else ""))
    
    if len(st.session_state.account_vault) > 1:
        st.divider()
        st.subheader("Account Vault")
        vault_users = list(st.session_state.account_vault.keys())
        current_idx = vault_users.index(st.session_state.username) if st.session_state.username in vault_users else 0
            
        switch_to = st.selectbox("Switch Account", vault_users, index=current_idx)
        if switch_to != st.session_state.username:
            st.session_state.update({"username": switch_to, "user_id": st.session_state.account_vault[switch_to], "library_config": None})
            if cookie_manager:
                try: cookie_manager.delete(cookie="library_access_code")
                except: pass
            st.rerun()

        st.caption("Remove account from vault:")
        for user in vault_users:
            if user != st.session_state.username and st.button(f"🗑️ Remove {user}", key=f"rem_{user}", use_container_width=True):
                del st.session_state.account_vault[user]
                st.rerun()

    if st.button("➕ Add Another Account", use_container_width=True):
        st.session_state.update({"adding_new_account": True, "logged_in": False, "library_config": None})
        if cookie_manager:
            try: cookie_manager.delete(cookie="library_access_code")
            except: pass
        st.rerun()

    st.divider()
    if st.session_state.library_config is not None:
        st.info(f"📋 Scope: `{st.session_state.library_config['name']}` ({st.session_state.library_config['type']})")
        if st.button("🔄 Change Access Code", use_container_width=True):
            st.session_state.library_config = None
            if cookie_manager:
                try: cookie_manager.delete(cookie="library_access_code")
                except: pass
            st.rerun()
            
    if st.button("Log Out Entire Session", type="primary", use_container_width=True):
        if cookie_manager:
            vault_cookie = cookie_manager.get(cookie="library_vault_tokens")
            if vault_cookie:
                with conn.session as s:
                    for t in vault_cookie.split(","):
                        if t.strip(): s.execute(text("DELETE FROM user_sessions WHERE token=:t"), {"t": t.strip()})
                    s.commit()
                try: cookie_manager.delete(cookie="library_vault_tokens")
                except: pass
            try: cookie_manager.delete(cookie="library_access_code")
            except: pass
        st.session_state.clear()
        st.rerun()
        
    with st.expander("👤 Account Security"):
        with st.form("change_password_form", clear_on_submit=True):
            curr_pass = st.text_input("Current Password", type="password")
            new_pass = st.text_input("New Password", type="password")
            conf_pass = st.text_input("Confirm Password", type="password")
            if st.form_submit_button("Update Password", use_container_width=True):
                if not curr_pass or not new_pass or not conf_pass: st.error("All fields required.")
                elif new_pass != conf_pass: st.error("Passwords do not match.")
                else:
                    db_pass = conn.query("SELECT password FROM users WHERE id=:id", params={"id": st.session_state.user_id}, ttl=0)
                    if not db_pass.empty and make_hashes(curr_pass) == db_pass.iloc[0]["password"]:
                        with conn.session as s:
                            s.execute(text("UPDATE users SET password=:p WHERE id=:id"), {"p": make_hashes(new_pass), "id": st.session_state.user_id})
                            s.commit()
                        st.success("Password changed!")
                    else: st.error("Incorrect current password.")

# ---------------- ADMIN PANEL ---------------- #
if is_admin:
    st.header("🛠️ Admin Management Dashboard")
    admin_tab1, admin_tab2, admin_tab3, admin_tab4 = st.tabs(["⚙️ Create Configs", "🔑 Code Registry", "👥 Accounts", "📋 Global Logs"])
    
    with admin_tab1:
        with st.form("admin_deploy_config"):
            lib_name = st.text_input("Library Name").strip()
            lib_code = st.text_input("Access Code").strip()
            col_a, col_b = st.columns(2)
            with col_a: lib_type = st.radio("Allocation", ["Singular", "Team"], horizontal=True)
            with col_b: cat_mode = st.radio("Category Mode", ["Default Only", "Custom Only", "Default + Custom"], horizontal=True)
            max_seats = st.number_input("Max Accounts", min_value=1, max_value=250, value=5)
            custom_cats = st.text_input("Custom Categories (Comma-separated)").strip()
            
            if st.form_submit_button("Deploy Library"):
                if not lib_name or not lib_code: st.error("Name and Code required.")
                elif cat_mode in ["Custom Only", "Default + Custom"] and not custom_cats: st.error("Custom Categories required for this mode.")
                else:
                    try:
                        with conn.session as s:
                            s.execute(text("INSERT INTO library_configurations (library_name, access_code, library_type, max_accounts, custom_categories, category_mode, created_at) VALUES (:n, :c, :lt, :ma, :cc, :cm, :cat)"),
                                      {"n": lib_name, "c": lib_code, "lt": lib_type, "ma": 1 if lib_type == "Singular" else int(max_seats), "cc": custom_cats, "cm": cat_mode, "cat": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                            s.commit()
                        st.success(f"Deployed! Code '{lib_code}' created.")
                        st.rerun()
                    except: st.error("Failed. Duplicate code?")

    with admin_tab2:
        configs = conn.query("SELECT id, library_name, access_code, library_type, max_accounts, category_mode FROM library_configurations ORDER BY id DESC", ttl=0)
        if not configs.empty:
            for _, r in configs.iterrows():
                # FIX: Cast r['id'] to Python int to prevent numpy.int64 database error
                occupied = conn.query("SELECT COUNT(*) as c FROM library_memberships WHERE config_id=:cid", params={"cid": int(r['id'])}, ttl=0).iloc[0]["c"]
                c1, c2 = st.columns([4, 1])
                with c1: st.markdown(f"🔹 **{r['access_code']}** | `{r['library_name']}` | Type: `{r['library_type']}` | Cats: `{r['category_mode']}` | Seats: `{occupied}/{r['max_accounts']}`")
                with c2:
                    if st.button("Delete", key=f"del_code_{r['id']}", type="secondary", use_container_width=True):
                        with conn.session as s:
                            # FIX: Cast r['id'] to Python int
                            s.execute(text("DELETE FROM library_configurations WHERE id=:id"), {"id": int(r['id'])})
                            s.commit()
                        st.rerun()
        else: st.info("No configs yet.")

    with admin_tab3:
        users = conn.query("SELECT u.id, u.username, u.registration_date, COUNT(b.id) as books FROM users u LEFT JOIN books b ON u.id = b.user_id GROUP BY u.id ORDER BY u.registration_date ASC", ttl=0)
        if not users.empty:
            st.dataframe(users.drop(columns=["id"]), use_container_width=True, hide_index=True)
            candidates = users[users["username"].str.lower() != "admin"]["username"].tolist()
            if candidates:
                target = st.selectbox("Select account to remove:", candidates)
                if st.button("🚨 Terminate Account", type="secondary"):
                    tid = int(users[users["username"] == target].iloc[0]["id"])
                    with conn.session as s:
                        s.execute(text("DELETE FROM users WHERE id=:id"), {"id": tid})
                        s.commit()
                    st.rerun()
        else: st.info("No users found.")

    with admin_tab4:
        books = conn.query("SELECT b.id, b.title, b.category, u.username FROM books b JOIN users u ON b.user_id = u.id ORDER BY b.id ASC", ttl=0)
        if not books.empty:
            for _, b in books.iterrows():
                b1, b2 = st.columns([3, 1])
                with b1: st.markdown(f"📖 **{b['title']}** | Cat: `{b['category']}` | Owner: `{b['username']}`")
                with b2:
                    if st.button("Purge", key=f"purge_{b['id']}", type="secondary"):
                        with conn.session as s:
                            # FIX: Cast b['id'] to Python int
                            s.execute(text("DELETE FROM books WHERE id=:id"), {"id": int(b['id'])})
                            s.commit()
                        st.rerun()
        else: st.info("No books globally.")
    st.divider()

# ---------------- GATEWAY VERIFICATION ---------------- #
if st.session_state.library_config is None:
    st.subheader("🔒 Target Access Verification Required")
    st.info("Enter configuration access code to open your layout.")
    
    entered_code = st.text_input("Access Code").strip()
    remember_code = st.checkbox("Remember code")
    if st.button("Verify & Open"):
        match = conn.query("SELECT id, library_name, library_type, max_accounts, custom_categories, category_mode FROM library_configurations WHERE access_code=:ac", params={"ac": entered_code}, ttl=0)
        if not match.empty:
            cfg = match.iloc[0]
            # FIX: Cast cfg['id'] to Python int here to prevent numpy crash
            members = conn.query("SELECT user_id FROM library_memberships WHERE config_id=:cid", params={"cid": int(cfg['id'])}, ttl=0)["user_id"].tolist()
            
            if is_admin or st.session_state.user_id in members or len(members) < cfg['max_accounts']:
                if not is_admin and st.session_state.user_id not in members:
                    with conn.session as s:
                        s.execute(text("INSERT INTO library_memberships (config_id, user_id, joined_at, is_leader) VALUES (:cid, :uid, :jat, :leader)"), {"cid": int(cfg['id']), "uid": st.session_state.user_id, "jat": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "leader": len(members) == 0})
                        s.commit()
                
                st.session_state.library_config = {"name": cfg['library_name'], "access_code": entered_code, "type": cfg['library_type'], "max_accounts": int(cfg['max_accounts']), "categories": compute_categories(cfg['category_mode'], cfg['custom_categories'])}
                if remember_code and cookie_manager:
                    try: cookie_manager.set(cookie="library_access_code", val=entered_code, expires_at=datetime.now() + pd.Timedelta(days=30))
                    except: pass
                st.rerun()
            else: st.error("❌ Workspace full.")
        else: st.error("Invalid configuration key.")
    st.stop()

# ---------------- CORE APPLICATION ---------------- #
cfg_id = int(conn.query("SELECT id FROM library_configurations WHERE access_code=:ac", params={"ac": st.session_state.library_config['access_code']}, ttl=0).iloc[0]["id"])
query_str = "SELECT b.id, b.title, b.category, b.image_bytes, b.image_name, u.username, b.user_id FROM books b JOIN users u ON b.user_id = u.id" + ("" if is_admin else " JOIN library_memberships lm ON b.user_id = lm.user_id AND b.config_id = lm.config_id") + " WHERE b.config_id = :cid ORDER BY b.id ASC"
books_list = [dict(r) for r in conn.session.execute(text(query_str), {"cid": cfg_id}).mappings()]
current_categories = st.session_state.library_config.get("categories", DEFAULT_CATEGORIES)

st.header(f"{dynamic_icon} Workspace: {st.session_state.library_config['name']}")

is_leader = not is_admin and not conn.query("SELECT is_leader FROM library_memberships WHERE user_id=:uid AND config_id=:cid AND is_leader=TRUE", params={"uid": st.session_state.user_id, "cid": cfg_id}, ttl=0).empty
members_df = conn.query("SELECT u.id, u.username, lm.is_leader FROM library_memberships lm JOIN users u ON lm.user_id = u.id WHERE lm.config_id=:cid", params={"cid": cfg_id}, ttl=0)

with st.sidebar:
    st.divider()
    with st.expander("👥 View Members"):
        if not members_df.empty:
            for _, m in members_df.iterrows(): st.markdown(f"- **{m['username']}** ({'👑 Leader' if m['is_leader'] else '👤'})")
        else: st.info("No members.")

    st.header("Add a Book")
    new_title = st.text_input("Title")
    new_cat = st.selectbox("Category", current_categories)
    new_file = st.file_uploader("Upload photo", type=["png", "jpg", "jpeg"])

    if st.button("Add Book", use_container_width=True):
        if not new_title.strip(): st.error("Enter title.")
        else:
            with conn.session as s:
                s.execute(text("INSERT INTO books (config_id, user_id, title, category, image_bytes, image_name) VALUES (:cid, :uid, :t, :c, :img, :n)"), {"cid": cfg_id, "uid": st.session_state.user_id, "t": new_title.strip(), "c": new_cat, "img": new_file.getvalue() if new_file else None, "n": new_file.name if new_file else None})
                s.commit()
            st.rerun()

    if st.session_state.library_config["type"] == "Team" and not is_admin:
        st.divider()
        st.subheader("🚪 Exit Scope")
        other_members = members_df[members_df["id"] != st.session_state.user_id]
        if is_leader and not other_members.empty:
            chosen = st.selectbox("Transfer Leadership To:", other_members["username"].tolist())
            if st.button("Transfer & Leave", type="secondary", use_container_width=True):
                new_id = int(other_members[other_members["username"] == chosen].iloc[0]["id"])
                with conn.session as s:
                    s.execute(text("UPDATE library_memberships SET is_leader=TRUE WHERE config_id=:cid AND user_id=:uid"), {"cid": cfg_id, "uid": new_id})
                    s.execute(text("DELETE FROM library_memberships WHERE config_id=:cid AND user_id=:uid"), {"cid": cfg_id, "uid": st.session_state.user_id})
                    s.commit()
                st.session_state.library_config = None
                if cookie_manager:
                    try: cookie_manager.delete(cookie="library_access_code")
                    except: pass
                st.rerun()
        else:
            if st.button("Leave Library", type="secondary", use_container_width=True):
                with conn.session as s:
                    s.execute(text("DELETE FROM library_memberships WHERE config_id=:cid AND user_id=:uid"), {"cid": cfg_id, "uid": st.session_state.user_id})
                    s.commit()
                st.session_state.library_config = None
                if cookie_manager:
                    try: cookie_manager.delete(cookie="library_access_code")
                    except: pass
                st.rerun()

    if books_list:
        st.divider()
        if st.button("Delete My Books", type="primary", use_container_width=True, disabled=not st.checkbox("Confirm wipe")):
            with conn.session as s:
                s.execute(text("DELETE FROM books WHERE config_id=:cid AND user_id=:uid"), {"cid": cfg_id, "uid": st.session_state.user_id})
                s.commit()
            st.rerun()

col1, col2 = st.columns([2, 1])
with col1:
    st.subheader("Library List")
    if books_list: st.dataframe(pd.DataFrame([{"Title": b["title"], "Category": b["category"], "Owner": b["username"]} for b in books_list]), use_container_width=True, hide_index=True)
    else: st.info("No books yet.")
with col2:
    st.subheader("Summary")
    if books_list: st.bar_chart(pd.DataFrame(books_list)["category"].value_counts().reindex(current_categories, fill_value=0))

st.divider()
st.subheader("Gallery")
if books_list:
    cols = st.columns(3)
    for i, b in enumerate(books_list):
        with cols[i % 3]:
            can_modify = is_admin or is_leader or b["user_id"] == st.session_state.user_id
            if st.session_state.editing_book_id == b["id"]:
                st.markdown("#### 📝 Edit")
                e_title = st.text_input("Title", value=b["title"], key=f"et_{b['id']}")
                e_cat = st.selectbox("Category", current_categories, index=current_categories.index(b["category"]) if b["category"] in current_categories else 0, key=f"ec_{b['id']}")
                e_file = st.file_uploader("Photo", type=["png", "jpg", "jpeg"], key=f"ef_{b['id']}")
                
                bc1, bc2 = st.columns(2)
                with bc1:
                    if st.button("Save", key=f"sv_{b['id']}", use_container_width=True):
                        if e_title.strip():
                            with conn.session as s:
                                # FIX: Cast b['id'] to int 
                                if e_file: s.execute(text("UPDATE books SET title=:t, category=:c, image_bytes=:img, image_name=:n WHERE id=:bid AND user_id=:uid"), {"t": e_title.strip(), "c": e_cat, "img": e_file.getvalue(), "n": e_file.name, "bid": int(b["id"]), "uid": st.session_state.user_id})
                                else: s.execute(text("UPDATE books SET title=:t, category=:c WHERE id=:bid AND user_id=:uid"), {"t": e_title.strip(), "c": e_cat, "bid": int(b["id"]), "uid": st.session_state.user_id})
                                s.commit()
                            st.session_state.editing_book_id = None
                            st.rerun()
                with bc2:
                    if st.button("Cancel", key=f"cl_{b['id']}", use_container_width=True):
                        st.session_state.editing_book_id = None
                        st.rerun()
            else:
                st.markdown(f"**{b['title']}**")
                st.caption(f"{b['category']} | Owner: {b['username']}")
                if b["image_bytes"]:
                    try: st.image(Image.open(io.BytesIO(bytes(b["image_bytes"]))), use_container_width=True)
                    except: st.caption("⚠️ [Image Error]")
                
                if can_modify:
                    ac1, ac2 = st.columns(2)
                    with ac1:
                        if st.button("📝 Edit", key=f"ed_{b['id']}", use_container_width=True):
                            st.session_state.editing_book_id = b["id"]
                            st.rerun()
                    with ac2:
                        if st.button("🗑️ Del", key=f"rm_{b['id']}", use_container_width=True):
                            with conn.session as s:
                                # FIX: Cast b['id'] to int
                                s.execute(text("DELETE FROM books WHERE id=:id"), {"id": int(b["id"])})
                                s.commit()
                            st.rerun()