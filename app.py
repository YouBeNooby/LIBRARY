from datetime import datetime
import hashlib
import io
import secrets
import extra_streamlit_components as stx
import pandas as pd
from PIL import Image
import streamlit as st
from sqlalchemy import text

# --- BASELINE INITIALIZATION ---
if "logged_in" not in st.session_state: st.session_state.logged_in = False
if "username" not in st.session_state: st.session_state.username = None
if "user_id" not in st.session_state: st.session_state.user_id = None
if "editing_book_id" not in st.session_state: st.session_state.editing_book_id = None
if "library_config" not in st.session_state: st.session_state.library_config = None
if "account_vault" not in st.session_state: st.session_state.account_vault = {}
if "adding_new_account" not in st.session_state: st.session_state.adding_new_account = False

conn = st.connection("postgresql", type="sql")
cookie_manager = stx.CookieManager()
DEFAULT_CATEGORIES = ["Read pending", "Reading in progress", "Already read", "Read again", "Give away", "Wishlist"]

# Helper to calculate final categories based on mode
def compute_categories(mode, raw_custom):
    custom_list = [c.strip() for c in raw_custom.split(",") if c.strip()] if raw_custom else []
    if mode == "Custom Only" and custom_list:
        return custom_list
    elif mode == "Default + Custom":
        return DEFAULT_CATEGORIES + custom_list
    return DEFAULT_CATEGORIES  # Fallback to default

# ACTIVE KICK-OUT TRANS-GUARD SYSTEM
if st.session_state.logged_in and st.session_state.library_config is not None:
    active_code = st.session_state.library_config.get("access_code")
    check_active_df = conn.query("SELECT library_name, library_type, max_accounts, custom_categories, category_mode FROM library_configurations WHERE access_code=:ac", params={"ac": active_code}, ttl=0)
    if check_active_df.empty:
        st.session_state.library_config = None
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

dynamic_title = "Book Library"
dynamic_icon = "📚"

if st.session_state.library_config is not None:
    dynamic_title = f"{st.session_state.library_config['name']} Tracker"
    dynamic_icon = "📖"
elif st.session_state.username == "admin":
    dynamic_title = "Admin Library Panel"
    dynamic_icon = "👑"

st.set_page_config(page_title=dynamic_title, page_icon=dynamic_icon, layout="wide")

# --- DATABASE SETUP (SAFE MODE) ---
def make_hashes(password):
    return hashlib.sha256(str.encode(password)).hexdigest()

def init_db():
    # 1. Safely create the base tables first
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

    # 2. ISOLATED: Try to add the new category column in its own bubble
    try:
        with conn.session as session:
            session.execute(text("ALTER TABLE library_configurations ADD COLUMN IF NOT EXISTS category_mode TEXT DEFAULT 'Default Only'"))
            session.commit()
    except Exception:
        pass

    # 3. Safely create the remaining tables
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

init_db()

# --- DATABASE FUNCTIONS ---
def add_user(username, password):
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with conn.session as session:
            session.execute(text("""
                INSERT INTO users (username, password, registration_date) 
                VALUES (:u, :p, :r)
            """), {"u": username, "p": make_hashes(password), "r": current_time})
            session.commit()
        return True
    except Exception:
        return False

def login_user(username, password):
    df = conn.query(
        "SELECT id, username FROM users WHERE username = :u AND password = :p",
        params={"u": username, "p": make_hashes(password)},
        ttl=0
    )
    if not df.empty:
        return (int(df.iloc[0]["id"]), df.iloc[0]["username"])
    return None

def update_user_password(user_id, new_password):
    with conn.session as session:
        session.execute(text("UPDATE users SET password = :p WHERE id = :id"), 
                        {"p": make_hashes(new_password), "id": user_id})
        session.commit()

def add_book_to_db(config_id, user_id, title, category, image_bytes, image_name):
    with conn.session as session:
        session.execute(text("""
            INSERT INTO books (config_id, user_id, title, category, image_bytes, image_name)
            VALUES (:cid, :uid, :t, :c, :img, :name)
        """), {"cid": config_id, "uid": user_id, "t": title, "c": category, "img": image_bytes, "name": image_name})
        session.commit()

def update_book_in_db(book_id, user_id, title, category, image_bytes=None, image_name=None):
    with conn.session as session:
        if image_bytes:
            session.execute(text("""
                UPDATE books 
                SET title = :t, category = :c, image_bytes = :img, image_name = :name
                WHERE id = :bid AND user_id = :uid
            """), {"t": title, "c": category, "img": image_bytes, "name": image_name, "bid": book_id, "uid": user_id})
        else:
            session.execute(text("""
                UPDATE books 
                SET title = :t, category = :c
                WHERE id = :bid AND user_id = :uid
            """), {"t": title, "c": category, "bid": book_id, "uid": user_id})
        session.commit()

def delete_book_from_db(book_id, user_id):
    with conn.session as session:
        session.execute(text("DELETE FROM books WHERE id = :bid AND user_id = :uid"), {"bid": book_id, "uid": user_id})
        session.commit()

def admin_global_delete_book(book_id):
    with conn.session as session:
        session.execute(text("DELETE FROM books WHERE id = :bid"), {"bid": book_id})
        session.commit()

def delete_all_books_from_db(config_id, user_id):
    with conn.session as session:
        session.execute(text("DELETE FROM books WHERE config_id = :cid AND user_id = :uid"), {"cid": config_id, "uid": user_id})
        session.commit()

def load_books_from_db(config_id, is_admin):
    with conn.session as session:
        if is_admin:
            query = """
                SELECT b.id, b.title, b.category, b.image_bytes, b.image_name, u.username
                FROM books b
                JOIN users u ON b.user_id = u.id
                WHERE b.config_id = :cid
                ORDER BY b.id ASC
            """
            result = session.execute(text(query), {"cid": config_id})
        else:
            query = """
                SELECT b.id, b.title, b.category, b.image_bytes, b.image_name, u.username
                FROM books b
                JOIN library_memberships lm ON b.user_id = lm.user_id AND b.config_id = lm.config_id
                JOIN users u ON b.user_id = u.id
                WHERE b.config_id = :cid
                ORDER BY b.id ASC
            """
            result = session.execute(text(query), {"cid": config_id})
        return [dict(row) for row in result.mappings()]

def admin_get_all_users_metrics():
    query = """
        SELECT 
            users.id AS db_id, 
            users.username AS "Username", 
            users.registration_date,
            COUNT(books.id) AS "Books Tracked"
        FROM users
        LEFT JOIN books ON users.id = books.user_id
        GROUP BY users.id, users.username, users.registration_date
        ORDER BY users.registration_date ASC
    """
    df = conn.query(query, ttl=0)
    if df.empty: return []
    df = df.sort_values("registration_date")
    df.insert(0, "User No.", range(1, len(df) + 1))
    return df.to_dict(orient="records")

def admin_get_all_books():
    query = """
        SELECT 
            books.id AS book_id,
            users.username AS "Owner", 
            books.title AS "Title", 
            books.category AS "Category"
        FROM books
        JOIN users ON books.user_id = users.id
        ORDER BY books.id ASC
    """
    df = conn.query(query, ttl=0)
    return df.to_dict(orient="records")

def admin_delete_user_and_library(target_user_id):
    with conn.session as session:
        try:
            session.execute(text("DELETE FROM user_sessions WHERE user_id = :uid"), {"uid": target_user_id})
            session.execute(text("DELETE FROM library_memberships WHERE user_id = :uid"), {"uid": target_user_id})
            session.execute(text("DELETE FROM books WHERE user_id = :uid"), {"uid": target_user_id})
            session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": target_user_id})
            session.commit()
            st.success("User and all associated data successfully deleted.")
        except Exception as e:
            session.rollback()
            st.error(f"Failed to delete user: {e}")

# --- AUTO-LOGIN / COOKIE LOGIC ---
if not st.session_state.logged_in and not st.session_state.adding_new_account:
    cookie_token = cookie_manager.get(cookie="book_library_token")
    if cookie_token:
        token_check = conn.query("SELECT user_id, username FROM user_sessions WHERE token = :t", params={"t": cookie_token}, ttl=0)
        if not token_check.empty:
            username = token_check.iloc[0]["username"]
            user_id = int(token_check.iloc[0]["user_id"])
            st.session_state.logged_in = True
            st.session_state.user_id = user_id
            st.session_state.username = username
            st.session_state.account_vault[username] = user_id

if st.session_state.logged_in and st.session_state.library_config is None:
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

# --- AUTHENTICATION & VAULT UI ---
if not st.session_state.logged_in:
    st.title("📚 Book Library")
    
    if st.session_state.adding_new_account:
        st.subheader("Add an Account to your Vault")
    else:
        st.subheader("Please Login or Register to access your collection")
        
    auth_mode = st.radio("Choose Action", ["Login", "Register"], horizontal=True, key="auth_radio", on_change=st.rerun)
    
    with st.form("auth_form", clear_on_submit=True):
        username = st.text_input("Username", key="auth_username").strip()
        password = st.text_input("Password", type="password", key="auth_password")
        remember_me = False
        if auth_mode == "Login":
            remember_me = st.checkbox("Keep me logged in")
        submit_auth = st.form_submit_button(auth_mode)
        
        if submit_auth:
            if not username or not password:
                st.error("Please fill in all fields.")
            elif auth_mode == "Register":
                if add_user(username, password):
                    st.success("Registration successful! You can now switch to Login.")
                else:
                    st.error("Username already taken or network issue occurred.")
            elif auth_mode == "Login":
                user_record = login_user(username, password)
                if user_record:
                    st.session_state.adding_new_account = False
                    st.session_state.account_vault[username] = user_record[0]
                    st.session_state.logged_in = True
                    st.session_state.user_id = user_record[0]
                    st.session_state.username = username
                    
                    if remember_me:
                        secure_token = secrets.token_urlsafe(32)
                        current_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        with conn.session as session:
                            session.execute(text("INSERT INTO user_sessions (token, user_id, username, created_at) VALUES (:t, :uid, :u, :c)"), 
                                            {"t": secure_token, "uid": user_record[0], "u": username, "c": current_ts})
                            session.commit()
                        cookie_manager.set(cookie="book_library_token", val=secure_token, expires_at=datetime.now() + pd.Timedelta(days=30))
                    st.rerun()
                else:
                    st.error("Invalid username or password.")
                    
    if st.session_state.adding_new_account:
        if st.button("Cancel & Return to Vault", use_container_width=True):
            st.session_state.adding_new_account = False
            st.session_state.logged_in = True
            st.rerun()
            
    st.stop()

is_admin = st.session_state.username.lower() == "admin"

# ---------------- GLOBAL MANAGEMENT SIDEBAR ---------------- #
with st.sidebar:
    st.header("Control Panel")
    st.success(f"Active: **{st.session_state.username}**" + (" *(Admin)*" if is_admin else ""))
    
    # --- ACCOUNT VAULT SWITCHER & REMOVER ---
    if len(st.session_state.account_vault) > 1:
        st.divider()
        st.subheader("Account Vault")
        vault_users = list(st.session_state.account_vault.keys())
        current_idx = vault_users.index(st.session_state.username) if st.session_state.username in vault_users else 0
            
        switch_to = st.selectbox("Switch Account", vault_users, index=current_idx)
        if switch_to != st.session_state.username:
            st.session_state.username = switch_to
            st.session_state.user_id = st.session_state.account_vault[switch_to]
            st.session_state.library_config = None
            try: cookie_manager.delete(cookie="library_access_code")
            except: pass
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
        if "auth_username" in st.session_state: del st.session_state["auth_username"]
        if "auth_password" in st.session_state: del st.session_state["auth_password"]
        try: cookie_manager.delete(cookie="library_access_code")
        except: pass
        st.rerun()

    st.divider()
    if st.session_state.library_config is not None:
        st.info(f"📋 Scope: `{st.session_state.library_config['name']}` ({st.session_state.library_config['type']})")
        if st.button("🔄 Change Access Code", use_container_width=True):
            st.session_state.library_config = None
            try: cookie_manager.delete(cookie="library_access_code")
            except: pass
            st.rerun()
            
    if st.button("Log Out Entire Session", type="primary", use_container_width=True):
        active_cookie = cookie_manager.get(cookie="book_library_token")
        if active_cookie:
            with conn.session as session:
                session.execute(text("DELETE FROM user_sessions WHERE token = :t"), {"t": active_cookie})
                session.commit()
            cookie_manager.delete(cookie="book_library_token")
        try: cookie_manager.delete(cookie="library_access_code")
        except: pass
        st.session_state.clear()
        st.query_params.clear()
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
                    user_data_df = conn.query("SELECT password FROM users WHERE id=:id", params={"id": st.session_state.user_id}, ttl=0)
                    if not user_data_df.empty and make_hashes(current_password) == user_data_df.iloc[0]["password"]:
                        update_user_password(st.session_state.user_id, new_password)
                        st.success("Password changed successfully!")
                    else:
                        st.error("Incorrect current password.")

# --- ADMIN PANEL ---
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
                cfg_id, cfg_name, cfg_code, cfg_type, cfg_max, cfg_cat_mode = cfg_row["id"], cfg_row["library_name"], cfg_row["access_code"], cfg_row["library_type"], cfg_row["max_accounts"], cfg_row["category_mode"]
                member_metrics_df = conn.query("SELECT COUNT(*) as count FROM library_memberships WHERE config_id=:cid", params={"cid": cfg_id}, ttl=0)
                occupied_seats = member_metrics_df.iloc[0]["count"]
                c_col1, c_col2 = st.columns([4, 1])
                with c_col1:
                    st.markdown(f"🔹 **{cfg_code}** | `{cfg_name}` | Type: `{cfg_type}` | Categories: `{cfg_cat_mode}` | Seats: `{occupied_seats} / {cfg_max}`")
                with c_col2:
                    if st.button("Delete Code", key=f"del_code_{cfg_id}", type="secondary", use_container_width=True):
                        with conn.session as session:
                            session.execute(text("DELETE FROM library_configurations WHERE id=:id"), {"id": cfg_id})
                            session.commit()
                        st.rerun()
        else: st.info("No customized setup mappings provisioned yet.")
    with admin_tab3:
        st.subheader("System Users Overview")
        user_metrics = admin_get_all_users_metrics()
        if user_metrics:
            display_df = pd.DataFrame(user_metrics).drop(columns=["db_id"])
            st.dataframe(display_df, use_container_width=True, hide_index=True)
            delete_candidates = [u["Username"] for u in user_metrics if u["Username"].lower() != "admin"]
            if delete_candidates:
                target_username = st.selectbox("Select account to remove:", delete_candidates)
                if st.button("🚨 Terminate Account", type="secondary"):
                    target_id = next(u["db_id"] for u in user_metrics if u["Username"] == target_username)
                    admin_delete_user_and_library(target_id)
                    st.rerun()
        else: st.info("No system users found.")
    with admin_tab4:
        st.subheader("Global Library Master Logs")
        all_books = admin_get_all_books()
        if all_books:
            for book_row in all_books:
                b_id, b_owner, b_title, b_cat = book_row["book_id"], book_row["Owner"], book_row["Title"], book_row["Category"]
                b_col1, b_col2 = st.columns([3, 1])
                with b_col1: st.markdown(f"📖 **{b_title}** | Category: `{b_cat}` | Owner: `{b_owner}`")
                with b_col2:
                    if st.button("Purge Book", key=f"admin_purge_bk_{b_id}", type="secondary"):
                        admin_global_delete_book(b_id)
                        st.rerun()
        else: st.info("No books recorded platform-wide.")
    st.divider()

# --- GATEWAY / WORKSPACE VERIFICATION ---
if st.session_state.library_config is None:
    st.subheader("🔒 Target Access Verification Required")
    st.info("Please enter your venue configuration access code to open your layout tracking panels.")
    with st.form("gateway_verification_code_form", clear_on_submit=True):
        entered_code = st.text_input("Enter Access Code Key").strip()
        remember_code = st.checkbox("Remember this access code")
        if st.form_submit_button("Verify & Mount Storage Scope Layout"):
            is_first_member = False
            match_df = conn.query("SELECT id, library_name, library_type, max_accounts, custom_categories, category_mode FROM library_configurations WHERE access_code=:ac", params={"ac": entered_code}, ttl=0)
            if not match_df.empty:
                cfg_id = int(match_df.iloc[0]["id"])
                cfg_name, cfg_type, cfg_max = match_df.iloc[0]["library_name"], match_df.iloc[0]["library_type"], int(match_df.iloc[0]["max_accounts"])
                
                cfg_cats = compute_categories(match_df.iloc[0]["category_mode"], match_df.iloc[0]["custom_categories"])
                
                membership_log_df = conn.query("SELECT user_id FROM library_memberships WHERE config_id=:cid", params={"cid": cfg_id}, ttl=0)
                registered_member_ids = membership_log_df["user_id"].tolist() if not membership_log_df.empty else []
                
                grant_token_entry = False
                if is_admin:
                    grant_token_entry = True
                elif st.session_state.user_id in registered_member_ids:
                    grant_token_entry = True
                else:
                    if len(registered_member_ids) >= cfg_max:
                        st.error("❌ Access Claim Refused. This workspace has reached its limit.")
                    else:
                        is_first_member = (len(registered_member_ids) == 0)
                        grant_token_entry = True
                        with conn.session as session:
                            session.execute(text("INSERT INTO library_memberships (config_id, user_id, joined_at, is_leader) VALUES (:cid, :uid, :jat, :leader)"), 
                                            {"cid": cfg_id, "uid": st.session_state.user_id, "jat": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "leader": is_first_member})
                            session.commit()
                
                if grant_token_entry:
                    st.session_state.library_config = {"name": cfg_name, "access_code": entered_code, "type": cfg_type, "max_accounts": cfg_max, "categories": cfg_cats}
                    if remember_code: cookie_manager.set(cookie="library_access_code", val=entered_code, expires_at=datetime.now() + pd.Timedelta(days=30))
                    st.success("Access granted!")
                    st.rerun()
            else: st.error("Invalid configuration key.")
    st.stop()

# --- RUNTIME CORE APPLICATION ---
match_df = conn.query("SELECT id FROM library_configurations WHERE access_code=:ac", params={"ac": st.session_state.library_config['access_code']}, ttl=0)
cfg_id = int(match_df.iloc[0]["id"])
books_list = load_books_from_db(cfg_id, is_admin)
current_categories = st.session_state.library_config.get("categories", DEFAULT_CATEGORIES)

st.header(f"{dynamic_icon} Workspace: {st.session_state.library_config['name']}")

leader_df = conn.query("SELECT is_leader FROM library_memberships WHERE user_id = :uid AND config_id = :cid", params={"uid": st.session_state.user_id, "cid": cfg_id}, ttl=0)
is_leader = leader_df.iloc[0]["is_leader"] if not leader_df.empty else False
members_df = conn.query("SELECT u.id, u.username, lm.is_leader FROM library_memberships lm JOIN users u ON lm.user_id = u.id WHERE lm.config_id = :cid", params={"cid": cfg_id}, ttl=0)

with st.sidebar:
    st.divider()
    st.header("Workspace Tools")
    with st.expander("👥 View Everyone in Library"):
        if not members_df.empty:
            for _, m in members_df.iterrows():
                st.markdown(f"- **{m['username']}** ({'👑 Leader' if m['is_leader'] else '👤 Member'})")
        else: st.info("No members recorded.")

    st.divider()
    st.header("Add a Book")
    title = st.text_input("Book title")
    category = st.selectbox("Category", current_categories, key="add_category")
    uploaded_file = st.file_uploader("Upload book photo", type=["png", "jpg", "jpeg"], key="add_photo")

    if st.button("Add Book", use_container_width=True):
        if title.strip() == "": st.error("Please enter a book title.")
        else:
            add_book_to_db(cfg_id, st.session_state.user_id, title.strip(), category, uploaded_file.getvalue() if uploaded_file else None, uploaded_file.name if uploaded_file else None)
            st.success(f"Added: {title}")
            st.rerun()

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
                    session.execute(text("DELETE FROM library_memberships WHERE config_id = :cid AND user_id = :uid"), {"cid": cfg_id, "uid": st.session_state.user_id})
                    session.commit()
                st.session_state.library_config = None
                try: cookie_manager.delete(cookie="library_access_code")
                except: pass
                st.rerun()
        else:
            if st.button("Leave Library", type="secondary", use_container_width=True):
                with conn.session as session:
                    session.execute(text("DELETE FROM library_memberships WHERE config_id = :cid AND user_id = :uid"), {"cid": cfg_id, "uid": st.session_state.user_id})
                    session.commit()
                st.session_state.library_config = None
                try: cookie_manager.delete(cookie="library_access_code")
                except: pass
                st.rerun()

    if books_list:
        st.divider()
        st.subheader("⚠️ Danger Zone")
        if st.button("Delete My Books", type="primary", use_container_width=True, disabled=not st.checkbox("I want to clear my books in this library")):
            delete_all_books_from_db(cfg_id, st.session_state.user_id)
            st.rerun()

col1, col2 = st.columns([2, 1])
with col1:
    st.subheader("Library Books")
    if books_list:
        st.dataframe(pd.DataFrame([{"Title": b["title"], "Category": b["category"], "Owner": b["username"], "Has Photo": "Yes" if b["image_bytes"] else "No"} for b in books_list]), use_container_width=True, hide_index=True)
    else: st.info("No books added to this library scope yet.")
with col2:
    st.subheader("Category Summary")
    if books_list: st.bar_chart(pd.DataFrame(books_list)["category"].value_counts().reindex(current_categories, fill_value=0))
    else: st.write("Add books to see the summary.")

st.divider()
st.subheader("Book Gallery")

if books_list:
    gallery_cols = st.columns(3)
    for i, book in enumerate(books_list):
        with gallery_cols[i % 3]:
            can_modify = is_admin or is_leader or (book["user_id"] == st.session_state.user_id)
            if st.session_state.editing_book_id == book["id"]:
                st.markdown(f"#### 📝 Edit Details")
                with st.form(f"edit_form_{book['id']}", clear_on_submit=True):
                    edit_title = st.text_input("Book Title", value=book["title"])
                    edit_category = st.selectbox("Category", current_categories, index=current_categories.index(book["category"]) if book["category"] in current_categories else 0)
                    edit_file = st.file_uploader("Replace Book Photo", type=["png", "jpg", "jpeg"])
                    
                    btn_save, btn_cancel = st.columns(2)
                    with btn_save: save_changes = st.form_submit_button("Save", use_container_width=True)
                    with btn_cancel: cancel_changes = st.form_submit_button("Cancel", use_container_width=True)
                    
                    if save_changes and edit_title.strip() != "":
                        update_book_in_db(book["id"], st.session_state.user_id, edit_title.strip(), edit_category, edit_file.getvalue() if edit_file else None, edit_file.name if edit_file else None)
                        st.session_state.editing_book_id = None
                        st.rerun()
                    if cancel_changes:
                        st.session_state.editing_book_id = None
                        st.rerun()
            else:
                st.markdown(f"**{book['title']}**")
                st.caption(f"Category: {book['category']} | Owner: {book['username']}")
                if book["image_bytes"]:
                    try: st.image(Image.open(io.BytesIO(bytes(book["image_bytes"]))), use_container_width=True)
                    except: st.caption("⚠️ [Image Error]")
                else: st.write("No photo uploaded.")
            
            action_edit, action_del = st.columns(2)
            if can_modify:
                with action_edit:
                    if st.button(f"📝 Edit", key=f"edit_btn_{book['id']}", use_container_width=True):
                        st.session_state.editing_book_id = book["id"]
                        st.rerun()
                with action_del:
                    if st.button(f"🗑️ Delete", key=f"del_{book['id']}", use_container_width=True):
                        delete_book_from_db(book["id"], st.session_state.user_id)
                        st.rerun()
else: st.write("Upload some books to display them here.")