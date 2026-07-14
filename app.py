from datetime import datetime
import hashlib
import io
import secrets
import extra_streamlit_components as stx
import pandas as pd
from PIL import Image
import streamlit as st
from sqlalchemy import text

# ==========================================
# 1. BASELINE INITIALIZATION & STATE
# ==========================================
if "logged_in" not in st.session_state: st.session_state.logged_in = False
if "username" not in st.session_state: st.session_state.username = None
if "user_id" not in st.session_state: st.session_state.user_id = None
if "editing_book_id" not in st.session_state: st.session_state.editing_book_id = None
if "library_config" not in st.session_state: st.session_state.library_config = None
if "account_vault" not in st.session_state: st.session_state.account_vault = {}
if "adding_new_account" not in st.session_state: st.session_state.adding_new_account = False

# Database and Cookie setup
conn = st.connection("postgresql", type="sql")
try:
    cookie_manager = stx.CookieManager()
except Exception:
    cookie_manager = None

DEFAULT_CATEGORIES = ["Read pending", "Reading in progress", "Already read", "Read again", "Give away", "Wishlist"]

def compute_categories(mode, raw_custom):
    custom_list = [c.strip() for c in raw_custom.split(",") if c.strip()] if raw_custom else []
    if mode == "Custom Only" and custom_list:
        return custom_list
    elif mode == "Default + Custom":
        return DEFAULT_CATEGORIES + custom_list
    return DEFAULT_CATEGORIES

def make_hashes(password):
    return hashlib.sha256(str.encode(password)).hexdigest()

# ==========================================
# 2. PAGE CONFIGURATION
# ==========================================
dynamic_title = "Book Library"
dynamic_icon = "📚"

if st.session_state.library_config is not None:
    dynamic_title = f"{st.session_state.library_config['name']} Tracker"
    dynamic_icon = "📖"
elif st.session_state.username == "admin":
    dynamic_title = "Admin Library Panel"
    dynamic_icon = "👑"

st.set_page_config(page_title=dynamic_title, page_icon=dynamic_icon, layout="wide")

# ==========================================
# 3. DATABASE INITIALIZATION
# ==========================================
def init_db():
    with conn.session as session:
        session.execute(text("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                registration_date TEXT NOT NULL
            )
        """))
        session.execute(text("""
            CREATE TABLE IF NOT EXISTS library_configurations (
                id SERIAL PRIMARY KEY,
                library_name TEXT NOT NULL,
                access_code TEXT UNIQUE NOT NULL,
                library_type TEXT NOT NULL DEFAULT 'Singular',
                max_accounts INTEGER NOT NULL DEFAULT 1,
                custom_categories TEXT,
                created_at TEXT NOT NULL
            )
        """))
        session.commit()

    try:
        with conn.session as session:
            session.execute(text("ALTER TABLE library_configurations ADD COLUMN IF NOT EXISTS category_mode TEXT DEFAULT 'Default Only'"))
            session.commit()
    except Exception:
        pass

    with conn.session as session:
        session.execute(text("""
            CREATE TABLE IF NOT EXISTS library_memberships (
                id SERIAL PRIMARY KEY,
                config_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                joined_at TEXT NOT NULL,
                is_leader BOOLEAN DEFAULT FALSE,
                UNIQUE (config_id, user_id),
                FOREIGN KEY (config_id) REFERENCES library_configurations(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """))
        session.execute(text("""
            CREATE TABLE IF NOT EXISTS books (
                id SERIAL PRIMARY KEY,
                config_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                image_bytes BYTEA,
                image_name TEXT,
                FOREIGN KEY (config_id) REFERENCES library_configurations(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """))
        session.execute(text("""
            CREATE TABLE IF NOT EXISTS user_sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """))
        session.commit()

# Ensure this only runs once per session to prevent database lockups
if "db_initialized" not in st.session_state:
    init_db()
    st.session_state.db_initialized = True

# ==========================================
# 4. ACTIVE KICK-OUT GUARD
# ==========================================
if st.session_state.logged_in and st.session_state.library_config is not None:
    active_code = st.session_state.library_config.get("access_code")
    check_active_df = conn.query("SELECT library_name, library_type, max_accounts, custom_categories, category_mode FROM library_configurations WHERE access_code=:ac", params={"ac": active_code}, ttl=0)
    if check_active_df.empty:
        st.session_state.library_config = None
        if cookie_manager:
            try: cookie_manager.delete(cookie="library_access_code")
            except Exception: pass
        st.warning("⚠️ The active session configuration access code was deleted by an administrator.")
    else:
        st.session_state.library_config["name"] = check_active_df.iloc[0]["library_name"]
        st.session_state.library_config["type"] = check_active_df.iloc[0]["library_type"]
        st.session_state.library_config["max_accounts"] = int(check_active_df.iloc[0]["max_accounts"])
        st.session_state.library_config["categories"] = compute_categories(
            check_active_df.iloc[0]["category_mode"], 
            check_active_df.iloc[0]["custom_categories"]
        )

# ==========================================
# 5. MULTI-VAULT COOKIE LOGIC
# ==========================================
if not st.session_state.logged_in and not st.session_state.adding_new_account and cookie_manager:
    try:
        vault_cookie = cookie_manager.get(cookie="library_vault_tokens")
        if vault_cookie:
            tokens = vault_cookie.split(",")
            for t in tokens:
                if not t.strip(): continue
                token_check = conn.query("SELECT user_id, username FROM user_sessions WHERE token = :t", params={"t": t.strip()}, ttl=0)
                if not token_check.empty:
                    st.session_state.account_vault[token_check.iloc[0]["username"]] = int(token_check.iloc[0]["user_id"])
            
            # Auto-login to the first account found
            if st.session_state.account_vault:
                st.session_state.logged_in = True
                first_user = list(st.session_state.account_vault.keys())[0]
                st.session_state.username = first_user
                st.session_state.user_id = st.session_state.account_vault[first_user]
    except Exception:
        pass

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
    except Exception:
        pass

# Ensure URL parameter hacks are completely locked down
st.query_params.clear()

# ==========================================
# 6. LOGIN & REGISTRATION UI (SAFE MODE)
# ==========================================
if not st.session_state.logged_in:
    st.title("📚 Book Library")
    
    if st.session_state.adding_new_account:
        st.subheader("Add an Account to your Vault")
    else:
        st.subheader("Please Login or Register to access your collection")
        
    auth_mode = st.radio("Choose Action", ["Login", "Register"], horizontal=True)
    
    username = st.text_input("Username").strip()
    password = st.text_input("Password", type="password")
    
    if auth_mode == "Register":
        if st.button("Create Account", type="primary"):
            if not username or not password:
                st.error("Please fill in all fields.")
            else:
                try:
                    with conn.session as session:
                        session.execute(text("INSERT INTO users (username, password, registration_date) VALUES (:u, :p, :r)"), 
                                        {"u": username, "p": make_hashes(password), "r": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                        session.commit()
                    st.success("Registration successful! You can now switch to Login.")
                except Exception:
                    st.error("Username already taken or network issue occurred.")
    
    elif auth_mode == "Login":
        remember_me = st.checkbox("Keep me logged in")
        if st.button("Login", type="primary"):
            if not username or not password:
                st.error("Please fill in all fields.")
            else:
                user_df = conn.query("SELECT id, username FROM users WHERE username = :u AND password = :p", params={"u": username, "p": make_hashes(password)}, ttl=0)
                if not user_df.empty:
                    uid = int(user_df.iloc[0]["id"])
                    st.session_state.adding_new_account = False
                    st.session_state.account_vault[username] = uid
                    st.session_state.logged_in = True
                    st.session_state.user_id = uid
                    st.session_state.username = username
                    
                    if remember_me and cookie_manager:
                        secure_token = secrets.token_urlsafe(32)
                        with conn.session as session:
                            session.execute(text("INSERT INTO user_sessions (token, user_id, username, created_at) VALUES (:t, :uid, :u, :c)"), 
                                            {"t": secure_token, "uid": uid, "u": username, "c": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                            session.commit()
                        
                        existing_cookie = cookie_manager.get(cookie="library_vault_tokens")
                        if existing_cookie:
                            new_cookie_val = f"{existing_cookie},{secure_token}"
                        else:
                            new_cookie_val = secure_token
                            
                        try:
                            cookie_manager.set(cookie="library_vault_tokens", val=new_cookie_val, expires_at=datetime.now() + pd.Timedelta(days=30))
                        except Exception:
                            pass
                    st.rerun()
                else:
                    st.error("Invalid username or password.")
                    
    if st.session_state.adding_new_account:
        if st.button("Cancel & Return to Vault", use_container_width=True):
            st.session_state.adding_new_account = False
            st.session_state.logged_in = True
            st.rerun()
            
    st.stop()

# ==========================================
# 7. GLOBAL MANAGEMENT SIDEBAR
# ==========================================
is_admin = st.session_state.username.lower() == "admin"

with st.sidebar:
    st.header("Control Panel")
    st.success(f"Active: **{st.session_state.username}**" + (" *(Admin)*" if is_admin else ""))
    
    # Vault Switcher
    if len(st.session_state.account_vault) > 1:
        st.divider()
        st.subheader("Account Vault")
        vault_users = list(st.session_state.account_vault.keys())
        current_idx = vault_users.index(st.session_state.username) if st.session_state.username in vault_users else 0
            
        switch_to = st.selectbox("Switch Account", vault_users, index=current_idx)
        if switch_to != st.session_state.username:
            st.session_state.username = switch_to
            st.session_state.user_id = int(st.session_state.account_vault[switch_to])
            st.session_state.library_config = None
            if cookie_manager:
                try: cookie_manager.delete(cookie="library_access_code")
                except Exception: pass
            st.rerun()

        st.caption("Remove account from vault:")
        for user in vault_users:
            if user != st.session_state.username:
                if st.button(f"🗑️ Remove {user}", key=f"rem_{user}", use_container_width=True):
                    del st.session_state.account_vault[user]
                    st.rerun()

    if st.button("➕ Add Another Account", use_container_width=True):
        st.session_state.adding_new_account = True
        st.session_state.logged_in = False
        st.session_state.library_config = None
        if cookie_manager:
            try: cookie_manager.delete(cookie="library_access_code")
            except Exception: pass
        st.rerun()

    st.divider()
    
    if st.session_state.library_config is not None:
        st.info(f"📋 Scope: `{st.session_state.library_config['name']}` ({st.session_state.library_config['type']})")
        if st.button("🔄 Change Access Code", use_container_width=True):
            st.session_state.library_config = None
            if cookie_manager:
                try: cookie_manager.delete(cookie="library_access_code")
                except Exception: pass
            st.rerun()
            
    if st.button("Log Out Entire Session", type="primary", use_container_width=True):
        if cookie_manager:
            vault_cookie = cookie_manager.get(cookie="library_vault_tokens")
            if vault_cookie:
                tokens = vault_cookie.split(",")
                with conn.session as session:
                    for t in tokens:
                        if t.strip():
                            session.execute(text("DELETE FROM user_sessions WHERE token = :t"), {"t": t.strip()})
                    session.commit()
                try: cookie_manager.delete(cookie="library_vault_tokens")
                except Exception: pass
            try: cookie_manager.delete(cookie="library_access_code")
            except Exception: pass
            
        st.session_state.clear()
        st.rerun()
        
    with st.expander("👤 Account Security"):
        st.subheader("Change Password")
        with st.form("change_password_form", clear_on_submit=True):
            current_password = st.text_input("Current Password", type="password")
            new_password = st.text_input("New Password", type="password")
            confirm_password = st.text_input("Confirm New Password", type="password")
            submit_change = st.form_submit_button("Update Password", use_container_width=True)
            
            if submit_change:
                if not current_password or not new_password or not confirm_password:
                    st.error("All password fields are required.")
                elif new_password != confirm_password:
                    st.error("New passwords do not match.")
                else:
                    user_data_df = conn.query("SELECT password FROM users WHERE id=:id", params={"id": int(st.session_state.user_id)}, ttl=0)
                    if not user_data_df.empty and make_hashes(current_password) == user_data_df.iloc[0]["password"]:
                        with conn.session as session:
                            session.execute(text("UPDATE users SET password = :p WHERE id = :id"), {"p": make_hashes(new_password), "id": int(st.session_state.user_id)})
                            session.commit()
                        st.success("Password changed successfully!")
                    else:
                        st.error("Incorrect current password.")

# ==========================================
# 8. ADMIN DASHBOARD
# ==========================================
if is_admin:
    st.header("🛠️ Admin Management Dashboard")
    admin_tab1, admin_tab2, admin_tab3, admin_tab4 = st.tabs([
        "⚙️ Create Configuration Keys",
        "🔑 Configured Access Registries",
        "👥 Platform Accounts Overview",
        "📋 Global Library Logs Master"
    ])
    
    with admin_tab1:
        st.subheader("Deploy Custom Library Configurations")
        with st.form("admin_deploy_config_form", clear_on_submit=True):
            lib_name_input = st.text_input("Configurable System / Library Name").strip()
            lib_code_input = st.text_input("Unique Entry Access Code Key").strip()
            
            col_a, col_b = st.columns(2)
            with col_a:
                lib_type_input = st.radio("Allocation Rules", ["Singular", "Team"], horizontal=True)
            with col_b:
                cat_mode_input = st.radio("Category Mode", ["Default Only", "Custom Only", "Default + Custom"], horizontal=True)
                
            max_seats_input = st.number_input("Maximum Allowed Team Members Accounts", min_value=1, max_value=250, value=5)
            custom_cats_input = st.text_input("Custom Categories (Comma-separated)").strip()
            
            if st.form_submit_button("Deploy Library Scope Configuration"):
                if not lib_name_input or not lib_code_input:
                    st.error("System Name and Access Code are strictly required.")
                elif cat_mode_input in ["Custom Only", "Default + Custom"] and not custom_cats_input:
                    st.error("You must provide Custom Categories for the selected mode.")
                else:
                    try:
                        resolved_seats = 1 if lib_type_input == "Singular" else int(max_seats_input)
                        with conn.session as session:
                            session.execute(text("""
                                INSERT INTO library_configurations 
                                (library_name, access_code, library_type, max_accounts, custom_categories, category_mode, created_at)
                                VALUES (:n, :c, :lt, :ma, :cc, :cm, :cat)
                            """), {
                                "n": lib_name_input, "c": lib_code_input, "lt": lib_type_input, "ma": resolved_seats, 
                                "cc": custom_cats_input, "cm": cat_mode_input, "cat": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            })
                            session.commit()
                        st.success(f"Configuration deployed! '{lib_code_input}' created.")
                        st.rerun()
                    except Exception:
                        st.error("Failed to deploy. Verify this code isn't a duplicate.")
                        
    with admin_tab2:
        st.subheader("Active System Access Codes Registry")
        all_configs_df = conn.query("SELECT id, library_name, access_code, library_type, max_accounts, category_mode FROM library_configurations ORDER BY id DESC", ttl=0)
        
        if not all_configs_df.empty:
            for _, cfg_row in all_configs_df.iterrows():
                cfg_id = int(cfg_row["id"]) # Type cast to avoid numpy errors
                member_metrics_df = conn.query("SELECT COUNT(*) as count FROM library_memberships WHERE config_id=:cid", params={"cid": cfg_id}, ttl=0)
                occupied_seats = member_metrics_df.iloc[0]["count"]
                
                c_col1, c_col2 = st.columns([4, 1])
                with c_col1:
                    st.markdown(f"🔹 **{cfg_row['access_code']}** | `{cfg_row['library_name']}` | Type: `{cfg_row['library_type']}` | Categories: `{cfg_row['category_mode']}` | Seats: `{occupied_seats} / {cfg_row['max_accounts']}`")
                with c_col2:
                    if st.button("Delete Code", key=f"del_code_{cfg_id}", type="secondary", use_container_width=True):
                        with conn.session as session:
                            session.execute(text("DELETE FROM library_configurations WHERE id=:id"), {"id": cfg_id})
                            session.commit()
                        st.rerun()
        else:
            st.info("No customized setup mappings provisioned yet.")
            
    with admin_tab3:
        st.subheader("System Users Overview")
        query = """
            SELECT users.id AS db_id, users.username AS "Username", users.registration_date, COUNT(books.id) AS "Books Tracked"
            FROM users LEFT JOIN books ON users.id = books.user_id
            GROUP BY users.id, users.username, users.registration_date ORDER BY users.registration_date ASC
        """
        user_metrics_df = conn.query(query, ttl=0)
        
        if not user_metrics_df.empty:
            st.dataframe(user_metrics_df.drop(columns=["db_id"]), use_container_width=True, hide_index=True)
            delete_candidates = user_metrics_df[user_metrics_df["Username"].str.lower() != "admin"]["Username"].tolist()
            if delete_candidates:
                target_username = st.selectbox("Select account to remove:", delete_candidates)
                if st.button("🚨 Terminate Account", type="secondary"):
                    target_id = int(user_metrics_df[user_metrics_df["Username"] == target_username].iloc[0]["db_id"])
                    with conn.session as session:
                        try:
                            session.execute(text("DELETE FROM user_sessions WHERE user_id = :uid"), {"uid": target_id})
                            session.execute(text("DELETE FROM library_memberships WHERE user_id = :uid"), {"uid": target_id})
                            session.execute(text("DELETE FROM books WHERE user_id = :uid"), {"uid": target_id})
                            session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": target_id})
                            session.commit()
                            st.success("User and all associated data successfully deleted.")
                        except Exception as e:
                            session.rollback()
                            st.error(f"Failed to delete user: {e}")
                    st.rerun()
        else:
            st.info("No system users found.")
            
    with admin_tab4:
        st.subheader("Global Library Master Logs")
        books_query = "SELECT books.id AS book_id, users.username AS \"Owner\", books.title AS \"Title\", books.category AS \"Category\" FROM books JOIN users ON books.user_id = users.id ORDER BY books.id ASC"
        all_books_df = conn.query(books_query, ttl=0)
        
        if not all_books_df.empty:
            for _, book_row in all_books_df.iterrows():
                b_id = int(book_row["book_id"])
                b_col1, b_col2 = st.columns([3, 1])
                with b_col1:
                    st.markdown(f"📖 **{book_row['Title']}** | Category: `{book_row['Category']}` | Owner: `{book_row['Owner']}`")
                with b_col2:
                    if st.button("Purge Book", key=f"admin_purge_bk_{b_id}", type="secondary"):
                        with conn.session as session:
                            session.execute(text("DELETE FROM books WHERE id = :bid"), {"bid": b_id})
                            session.commit()
                        st.rerun()
        else:
            st.info("No books recorded platform-wide.")
    st.divider()

# ==========================================
# 9. GATEWAY ACCESS VERIFICATION
# ==========================================
if st.session_state.library_config is None:
    st.subheader("🔒 Target Access Verification Required")
    st.info("Please enter your venue configuration access code to open your layout tracking panels.")
    
    with st.form("gateway_verification_code_form", clear_on_submit=True):
        entered_code = st.text_input("Enter Access Code Key").strip()
        remember_code = st.checkbox("Remember this access code")
        
        if st.form_submit_button("Verify & Mount Storage Scope Layout"):
            match_df = conn.query("SELECT id, library_name, library_type, max_accounts, custom_categories, category_mode FROM library_configurations WHERE access_code=:ac", params={"ac": entered_code}, ttl=0)
            
            if not match_df.empty:
                cfg_id = int(match_df.iloc[0]["id"]) # Type cast integer
                cfg_name = match_df.iloc[0]["library_name"]
                cfg_type = match_df.iloc[0]["library_type"]
                cfg_max = int(match_df.iloc[0]["max_accounts"])
                cfg_cats = compute_categories(match_df.iloc[0]["category_mode"], match_df.iloc[0]["custom_categories"])
                
                membership_log_df = conn.query("SELECT user_id FROM library_memberships WHERE config_id=:cid", params={"cid": cfg_id}, ttl=0)
                registered_member_ids = membership_log_df["user_id"].tolist() if not membership_log_df.empty else []
                
                grant_token_entry = False
                if is_admin or st.session_state.user_id in registered_member_ids:
                    grant_token_entry = True
                else:
                    if len(registered_member_ids) >= cfg_max:
                        st.error("❌ Access Claim Refused. This workspace has reached its limit.")
                    else:
                        is_first_member = (len(registered_member_ids) == 0)
                        grant_token_entry = True
                        with conn.session as session:
                            session.execute(text("INSERT INTO library_memberships (config_id, user_id, joined_at, is_leader) VALUES (:cid, :uid, :jat, :leader)"), 
                                            {"cid": cfg_id, "uid": int(st.session_state.user_id), "jat": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "leader": is_first_member})
                            session.commit()
                
                if grant_token_entry:
                    st.session_state.library_config = {"name": cfg_name, "access_code": entered_code, "type": cfg_type, "max_accounts": cfg_max, "categories": cfg_cats}
                    if remember_code and cookie_manager:
                        try:
                            cookie_manager.set(cookie="library_access_code", val=entered_code, expires_at=datetime.now() + pd.Timedelta(days=30))
                        except Exception: pass
                    st.success("Access granted!")
                    st.rerun()
            else:
                st.error("Invalid configuration key.")
    st.stop()

# ==========================================
# 10. CORE LIBRARY APPLICATION
# ==========================================
# Securely fetching books and converting to list of dicts. Ensuring cfg_id is INT.
raw_cfg = conn.query("SELECT id FROM library_configurations WHERE access_code=:ac", params={"ac": st.session_state.library_config['access_code']}, ttl=0)
cfg_id = int(raw_cfg.iloc[0]["id"])

if is_admin:
    query = "SELECT b.id, b.title, b.category, b.image_bytes, b.image_name, u.username, b.user_id FROM books b JOIN users u ON b.user_id = u.id WHERE b.config_id = :cid ORDER BY b.id ASC"
else:
    query = "SELECT b.id, b.title, b.category, b.image_bytes, b.image_name, u.username, b.user_id FROM books b JOIN library_memberships lm ON b.user_id = lm.user_id AND b.config_id = lm.config_id JOIN users u ON b.user_id = u.id WHERE b.config_id = :cid ORDER BY b.id ASC"

with conn.session as session:
    books_list = [dict(row) for row in session.execute(text(query), {"cid": cfg_id}).mappings()]

current_categories = st.session_state.library_config.get("categories", DEFAULT_CATEGORIES)

st.header(f"{dynamic_icon} Workspace: {st.session_state.library_config['name']}")

# Check membership metadata
leader_df = conn.query("SELECT is_leader FROM library_memberships WHERE user_id = :uid AND config_id = :cid", params={"uid": int(st.session_state.user_id), "cid": cfg_id}, ttl=0)
is_leader = leader_df.iloc[0]["is_leader"] if not leader_df.empty else False
members_df = conn.query("SELECT u.id, u.username, lm.is_leader FROM library_memberships lm JOIN users u ON lm.user_id = u.id WHERE lm.config_id = :cid", params={"cid": cfg_id}, ttl=0)

# Dashboard Tools Sidebar
with st.sidebar:
    st.divider()
    st.header("Workspace Tools")
    
    with st.expander("👥 View Everyone in Library"):
        if not members_df.empty:
            for _, m in members_df.iterrows():
                st.markdown(f"- **{m['username']}** ({'👑 Leader' if m['is_leader'] else '👤 Member'})")
        else:
            st.info("No members recorded.")

    st.divider()
    st.header("Add a Book")
    title_input = st.text_input("Book title")
    category_input = st.selectbox("Category", current_categories, key="add_category")
    uploaded_file = st.file_uploader("Upload book photo", type=["png", "jpg", "jpeg"], key="add_photo")

    if st.button("Add Book", use_container_width=True):
        if title_input.strip() == "":
            st.error("Please enter a book title.")
        else:
            with conn.session as session:
                session.execute(text("""
                    INSERT INTO books (config_id, user_id, title, category, image_bytes, image_name)
                    VALUES (:cid, :uid, :t, :c, :img, :name)
                """), {
                    "cid": cfg_id, 
                    "uid": int(st.session_state.user_id), 
                    "t": title_input.strip(), 
                    "c": category_input, 
                    "img": uploaded_file.getvalue() if uploaded_file else None, 
                    "name": uploaded_file.name if uploaded_file else None
                })
                session.commit()
            st.success(f"Added: {title_input}")
            st.rerun()

    # Team Exiting Logic
    if st.session_state.library_config["type"] == "Team" and not is_admin:
        st.divider()
        st.subheader("🚪 Exit Library Scope")
        other_members = members_df[members_df["id"] != st.session_state.user_id]
        
        if is_leader and not other_members.empty:
            st.warning("⚠️ You are the Leader. Select a new leader before leaving.")
            chosen_new_leader = st.selectbox("Transfer Leadership To:", other_members["username"].tolist())
            
            if st.button("Transfer & Leave Library", type="secondary", use_container_width=True):
                new_lead_id = int(other_members[other_members["username"] == chosen_new_leader]["id"].values[0])
                with conn.session as session:
                    session.execute(text("UPDATE library_memberships SET is_leader = TRUE WHERE config_id = :cid AND user_id = :uid"), {"cid": cfg_id, "uid": new_lead_id})
                    session.execute(text("DELETE FROM library_memberships WHERE config_id = :cid AND user_id = :uid"), {"cid": cfg_id, "uid": int(st.session_state.user_id)})
                    session.commit()
                st.session_state.library_config = None
                if cookie_manager:
                    try: cookie_manager.delete(cookie="library_access_code")
                    except Exception: pass
                st.rerun()
        else:
            if st.button("Leave Library", type="secondary", use_container_width=True):
                with conn.session as session:
                    session.execute(text("DELETE FROM library_memberships WHERE config_id = :cid AND user_id = :uid"), {"cid": cfg_id, "uid": int(st.session_state.user_id)})
                    session.commit()
                st.session_state.library_config = None
                if cookie_manager:
                    try: cookie_manager.delete(cookie="library_access_code")
                    except Exception: pass
                st.rerun()

    # Global Wipe for User in Scope
    if books_list:
        st.divider()
        st.subheader("⚠️ Danger Zone")
        if st.button("Delete My Books", type="primary", use_container_width=True, disabled=not st.checkbox("I want to clear my books in this library")):
            with conn.session as session:
                session.execute(text("DELETE FROM books WHERE config_id = :cid AND user_id = :uid"), {"cid": cfg_id, "uid": int(st.session_state.user_id)})
                session.commit()
            st.rerun()

# Dashboard Body Tables & Charts
col1, col2 = st.columns([2, 1])
with col1:
    st.subheader("Library Books")
    if books_list:
        df_display = pd.DataFrame([{"Title": b["title"], "Category": b["category"], "Owner": b["username"], "Has Photo": "Yes" if b["image_bytes"] else "No"} for b in books_list])
        st.dataframe(df_display, use_container_width=True, hide_index=True)
    else:
        st.info("No books added to this library scope yet.")
with col2:
    st.subheader("Category Summary")
    if books_list:
        counts = pd.DataFrame(books_list)["category"].value_counts().reindex(current_categories, fill_value=0)
        st.bar_chart(counts)
    else:
        st.write("Add books to see the summary.")

st.divider()
st.subheader("Book Gallery")

if books_list:
    gallery_cols = st.columns(3)
    for i, book in enumerate(books_list):
        b_id = int(book["id"])
        with gallery_cols[i % 3]:
            can_modify = is_admin or is_leader or (book["user_id"] == st.session_state.user_id)
            
            if st.session_state.editing_book_id == b_id:
                st.markdown(f"#### 📝 Edit Details")
                with st.form(f"edit_form_{b_id}", clear_on_submit=True):
                    edit_title = st.text_input("Book Title", value=book["title"])
                    cat_index = current_categories.index(book["category"]) if book["category"] in current_categories else 0
                    edit_category = st.selectbox("Category", current_categories, index=cat_index)
                    edit_file = st.file_uploader("Replace Book Photo", type=["png", "jpg", "jpeg"])
                    
                    btn_save, btn_cancel = st.columns(2)
                    with btn_save:
                        save_changes = st.form_submit_button("Save", use_container_width=True)
                    with btn_cancel:
                        cancel_changes = st.form_submit_button("Cancel", use_container_width=True)
                    
                    if save_changes and edit_title.strip() != "":
                        with conn.session as session:
                            if edit_file:
                                session.execute(text("""
                                    UPDATE books SET title = :t, category = :c, image_bytes = :img, image_name = :name
                                    WHERE id = :bid AND user_id = :uid
                                """), {"t": edit_title.strip(), "c": edit_category, "img": edit_file.getvalue(), "name": edit_file.name, "bid": b_id, "uid": int(st.session_state.user_id)})
                            else:
                                session.execute(text("""
                                    UPDATE books SET title = :t, category = :c
                                    WHERE id = :bid AND user_id = :uid
                                """), {"t": edit_title.strip(), "c": edit_category, "bid": b_id, "uid": int(st.session_state.user_id)})
                            session.commit()
                        st.session_state.editing_book_id = None
                        st.rerun()
                        
                    if cancel_changes:
                        st.session_state.editing_book_id = None
                        st.rerun()
            else:
                st.markdown(f"**{book['title']}**")
                st.caption(f"Category: {book['category']} | Owner: {book['username']}")
                if book["image_bytes"]:
                    try:
                        st.image(Image.open(io.BytesIO(bytes(book["image_bytes"]))), use_container_width=True)
                    except Exception:
                        st.caption("⚠️ [Image Error]")
                else:
                    st.write("No photo uploaded.")
            
            # Action Buttons
            action_edit, action_del = st.columns(2)
            if can_modify:
                with action_edit:
                    if st.button(f"📝 Edit", key=f"edit_btn_{b_id}", use_container_width=True):
                        st.session_state.editing_book_id = b_id
                        st.rerun()
                with action_del:
                    if st.button(f"🗑️ Delete", key=f"del_{b_id}", use_container_width=True):
                        with conn.session as session:
                            session.execute(text("DELETE FROM books WHERE id = :bid AND user_id = :uid"), {"bid": b_id, "uid": int(st.session_state.user_id)})
                            session.commit()
                        st.rerun()
else:
    st.write("Upload some books to display them here.")