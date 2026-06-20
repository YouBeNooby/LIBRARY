import hashlib
from datetime import datetime
import pandas as pd
import streamlit as st
from PIL import Image
import io
import secrets
from sqlalchemy import text
import extra_streamlit_components as stx

# --- BASELINE INITIALIZATION & PARAMETERS ISOLATION INTERCEPTIONS ---
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "username" not in st.session_state:
    st.session_state.username = None
if "user_id" not in st.session_state:
    st.session_state.user_id = None
if "editing_book_id" not in st.session_state:
    st.session_state.editing_book_id = None
if "library_config" not in st.session_state:
    st.session_state.library_config = None  # Dict layout format mapping tracking parameters

# 2. Establish Persistent Cloud Database Connection
conn = st.connection("postgresql", type="sql")

# Initialize Cookie Manager early so interceptors can read them
cookie_manager = stx.CookieManager()

# ACTIVE KICK-OUT TRANS-GUARD SYSTEM
if st.session_state.logged_in and st.session_state.library_config is not None:
    active_code = st.session_state.library_config.get("access_code")
    check_active_df = conn.query("SELECT library_name, library_type, max_accounts FROM library_configurations WHERE access_code=:ac", params={"ac": active_code}, ttl=0)
    if check_active_df.empty:
        st.session_state.library_config = None
        try:
            cookie_manager.delete(cookie="library_access_code")
        except Exception:
            pass
        st.warning("⚠️ The active session configuration access code was deleted by an administrator.")
    else:
        # Keep background data objects updated natively on runtimes
        st.session_state.library_config["name"] = check_active_df.iloc[0]["library_name"]
        st.session_state.library_config["type"] = check_active_df.iloc[0]["library_type"]
        st.session_state.library_config["max_accounts"] = int(check_active_df.iloc[0]["max_accounts"])

# 3. Dynamic Page Layout Title Mapping Construction Engine
dynamic_title = "Book Library"
dynamic_icon = "📚"

if st.session_state.library_config is not None:
    dynamic_title = f"{st.session_state.library_config['name']} Tracker"
    dynamic_icon = "📖"
elif st.session_state.username == "admin":
    dynamic_title = "Admin Library Panel"
    dynamic_icon = "👑"

st.set_page_config(page_title=dynamic_title, page_icon=dynamic_icon, layout="wide")

CATEGORIES = [
    "Read pending",
    "Reading in progress",
    "Already read", 
    "Read again", 
    "Give away", 
    "Wishlist"
]

def make_hashes(password):
    return hashlib.sha256(str.encode(password)).hexdigest()

# Initialize Tables on Supabase
def init_db():
    with conn.session as session:
        # Create users table
# Member seat assignment map tracking table configuration
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
        # Create books table
        session.execute(text("""
            CREATE TABLE IF NOT EXISTS books (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                image_bytes BYTEA,
                image_name TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """))
        # Create user sessions table (For secure server-side tracking)
        session.execute(text("""
            CREATE TABLE IF NOT EXISTS user_sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """))
        # Completely configurable access code mapping table with seat configuration data objects
        session.execute(text("""
            CREATE TABLE IF NOT EXISTS library_configurations (
                id SERIAL PRIMARY KEY,
                library_name TEXT NOT NULL,
                access_code TEXT UNIQUE NOT NULL,
                library_type TEXT NOT NULL DEFAULT 'Singular',
                max_accounts INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """))
        # Member seat assignment map tracking table configuration
        session.execute(text("""
            CREATE TABLE IF NOT EXISTS library_memberships (
                id SERIAL PRIMARY KEY,
                config_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                joined_at TEXT NOT NULL,
                UNIQUE (config_id, user_id),
                FOREIGN KEY (config_id) REFERENCES library_configurations(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """))
        session.commit()
        
        # Hardcoded Admin Account Insurance
        hashed_admin_password = make_hashes("LeBakri!!18")
        try:
            res = session.execute(text("SELECT id FROM users WHERE username = 'admin'")).fetchone()
            if not res:
                session.execute(text("""
                    INSERT INTO users (username, password, registration_date) 
                    VALUES (:u, :p, :r)
                """), {"u": "admin", "p": hashed_admin_password, "r": "2000-01-01 00:00:00"})
                session.commit()
            else:
                session.execute(text("UPDATE users SET password = :p WHERE username = 'admin'"), {"p": hashed_admin_password})
                session.commit()
        except Exception:
            session.rollback()


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


def add_book_to_db(user_id, title, category, image_bytes, image_name):
    with conn.session as session:
        session.execute(text("""
            INSERT INTO books (user_id, title, category, image_bytes, image_name)
            VALUES (:uid, :t, :c, :img, :name)
        """), {"uid": user_id, "t": title, "c": category, "img": image_bytes, "name": image_name})
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


def delete_all_books_from_db(user_id):
    with conn.session as session:
        session.execute(text("DELETE FROM books WHERE user_id = :uid"), {"uid": user_id})
        session.commit()


def load_books_from_db(config_id, is_admin, user_id):
    with conn.session as session:
        # If Admin, ignore the membership join. If member, use the join.
        if is_admin:
            query = """
                SELECT b.id, b.title, b.category, b.image_bytes, b.image_name, u.username
                FROM books b
                JOIN users u ON b.user_id = u.id
                ORDER BY b.id ASC
            """
            result = session.execute(text(query))
        else:
            query = """
                SELECT b.id, b.title, b.category, b.image_bytes, b.image_name, u.username
                FROM books b
                JOIN library_memberships lm ON b.user_id = lm.user_id
                JOIN users u ON b.user_id = u.id
                WHERE lm.config_id = :cid
                ORDER BY b.id ASC
            """
            result = session.execute(text(query), {"cid": config_id})
            
        return [dict(row) for row in result.mappings()]


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
        session.execute(text("DELETE FROM books WHERE user_id = :uid"), {"uid": target_user_id})
        session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": target_user_id})
        session.execute(text("DELETE FROM user_sessions WHERE user_id = :uid"), {"uid": target_user_id})
        session.commit()


# Trigger initial table checks on cloud environment
init_db()

# BROWSER COOKIE AUTO-LOGIN VERIFIER
if not st.session_state.logged_in:
    cookie_token = cookie_manager.get(cookie="book_library_token")
    
    if cookie_token:
        token_check = conn.query(
            "SELECT user_id, username FROM user_sessions WHERE token = :t",
            params={"t": cookie_token},
            ttl=0
        )
        if not token_check.empty:
            st.session_state.logged_in = True
            st.session_state.user_id = int(token_check.iloc[0]["user_id"])
            st.session_state.username = token_check.iloc[0]["username"]

# BROWSER COOKIE ACCESS CODE INTERCEPTOR
if st.session_state.logged_in and st.session_state.library_config is None:
    saved_code = cookie_manager.get(cookie="library_access_code")
    if saved_code:
        match_df = conn.query("SELECT id, library_name, library_type, max_accounts FROM library_configurations WHERE access_code=:ac", params={"ac": saved_code}, ttl=0)
        if not match_df.empty:
            st.session_state.library_config = {
                "name": match_df.iloc[0]["library_name"],
                "access_code": saved_code,
                "type": match_df.iloc[0]["library_type"],
                "max_accounts": int(match_df.iloc[0]["max_accounts"])
            }

# 4. Authentication UI Workflow
if not st.session_state.logged_in:
    st.title("📚 Book Library")
    st.subheader("Please Login or Register to access your collection")
    
    auth_mode = st.radio("Choose Action", ["Login", "Register"], horizontal=True)
    
    with st.form("auth_form"):
        username = st.text_input("Username", key=f"user_{auth_mode}").strip()
        password = st.text_input("Password", type="password", key=f"pass_{auth_mode}")
        
        remember_me = False
        if auth_mode == "Login":
            remember_me = st.checkbox("Keep me logged in", key="remember_Login")
            
        submit_auth = st.form_submit_button(auth_mode)
        
        if submit_auth:
            if not username or not password:
                st.error("Please fill in all fields.")
            elif auth_mode == "Register":
                if username.lower() == "admin":
                    st.error("The username 'admin' is a reserved system identifier.")
                elif add_user(username, password):
                    st.success("Registration successful! You can now switch to Login.")
                else:
                    st.error("Username already taken or network issue occurred.")
            elif auth_mode == "Login":
                user_record = login_user(username, password)
                if user_record:
                    st.session_state.logged_in = True
                    st.session_state.user_id = user_record[0]
                    st.session_state.username = user_record[1]
                    
                    if remember_me:
                        secure_token = secrets.token_urlsafe(32)
                        current_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        
                        # Write mapping to database
                        with conn.session as session:
                            session.execute(text("""
                                INSERT INTO user_sessions (token, user_id, username, created_at)
                                VALUES (:t, :uid, :u, :c)
                            """), {"t": secure_token, "uid": user_record[0], "u": user_record[1], "c": current_timestamp})
                            session.commit()
                            
                        cookie_manager.set(
                            cookie="book_library_token",
                            val=secure_token,
                            expires_at=datetime.now() + pd.Timedelta(days=30)
                        )
                    
                    st.query_params.clear()
                    st.rerun()
                else:
                    st.error("Invalid username or password.")
    st.stop()

# --- AUTHENTICATED SYSTEM LAYOUT BOUNDARIES ---
is_admin = st.session_state.username.lower() == "admin"

# ---------------- DEDICATED MANAGEMENT DECK SIDEBAR ---------------- #
with st.sidebar:
    st.header("Control Panel")
    st.success(f"User: **{st.session_state.username}**" + (" *(Admin)*" if is_admin else ""))
    
    if st.session_state.library_config is not None:
        st.info(f"📋 Scope: `{st.session_state.library_config['name']}` ({st.session_state.library_config['type']})")
    if st.button("🔄 Change Access Code", use_container_width=True):
            # Clear all session state related to the library
            st.session_state.library_config = None
            
            # Explicitly kill the cookie
            try:
                cookie_manager.delete(cookie="library_access_code")
            except:
                pass
            st.rerun()
            
    if st.button("Log Out", type="primary", use_container_width=True):
        active_cookie = cookie_manager.get(cookie="book_library_token")
        if active_cookie:
            with conn.session as session:
                session.execute(text("DELETE FROM user_sessions WHERE token = :t"), {"t": active_cookie})
                session.commit()
            cookie_manager.delete(cookie="book_library_token")
            
        try:
            cookie_manager.delete(cookie="library_access_code")
        except Exception:
            pass
                
        st.session_state.logged_in = False
        st.session_state.user_id = None
        st.session_state.username = None
        st.session_state.library_config = None
        st.session_state.editing_book_id = None
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

# ---------------- ADMIN RECONSTRUCTED EXECUTIVE PANEL ---------------- #
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
            lib_name_input = st.text_input("Configurable System / Library Name", placeholder="e.g., User's Library, Book Club Library").strip()
            lib_code_input = st.text_input("Unique Entry Access Code Key").strip()
            
            # ADMIN ALLOCATION SCALE PICKER INTERFACE COMPONENTS
            lib_type_input = st.radio("Operational Allocation Mapping Rules Profile Set", ["Singular", "Team"], horizontal=True)
            max_seats_input = st.number_input("Maximum Allowed Team Members Accounts (Ignored in Singular Mode)", min_value=1, max_value=250, value=5)
            
            submit_config = st.form_submit_button("Deploy Library Scope Configuration")
            
            if submit_config:
                if not lib_name_input or not lib_code_input:
                    st.error("All dynamic parameters are strictly required.")
                else:
                    try:
                        current_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        resolved_seats = 1 if lib_type_input == "Singular" else int(max_seats_input)
                        
                        with conn.session as session:
                            session.execute(text("""
                                INSERT INTO library_configurations (library_name, access_code, library_type, max_accounts, created_at)
                                VALUES (:n, :c, :lt, :ma, :cat)
                            """), {"n": lib_name_input, "c": lib_code_input, "lt": lib_type_input, "ma": resolved_seats, "cat": current_ts})
                            session.commit()
                        st.success(f"Configuration deployed! '{lib_code_input}' sets a `{lib_type_input}` storage setup (Max accounts: {resolved_seats}) for '{lib_name_input}'.")
                        st.rerun()
                    except Exception:
                        st.error("Failed to deploy layout rules. Verify this code isn't a duplicate registry item.")
                        
    with admin_tab2:
        st.subheader("Active System Access Codes Registry")
        all_configs_df = conn.query("SELECT id, library_name, access_code, library_type, max_accounts FROM library_configurations ORDER BY id DESC", ttl=0)
        if not all_configs_df.empty:
            for _, cfg_row in all_configs_df.iterrows():
                cfg_id = int(cfg_row["id"])
                cfg_name = cfg_row["library_name"]
                cfg_code = cfg_row["access_code"]
                cfg_type = cfg_row["library_type"]
                cfg_max = int(cfg_row["max_accounts"])
                
                # Dynamic intake registration counts metrics logs aggregation
                member_metrics_df = conn.query("SELECT COUNT(*) as count FROM library_memberships WHERE config_id=:cid", params={"cid": cfg_id}, ttl=0)
                occupied_seats = member_metrics_df.iloc[0]["count"]
                
                c_col1, c_col2 = st.columns([3, 1])
                with c_col1:
                    st.markdown(f"🔹 Code Key: **{cfg_code}** | Target: `{cfg_name}` | Profile Type: `{cfg_type}` | Active Seat Allocations: `{occupied_seats} / {cfg_max}`")
                with c_col2:
                    if st.button("Delete Configuration Code", key=f"del_code_{cfg_id}", type="secondary", use_container_width=True):
                        with conn.session as session:
                            session.execute(text("DELETE FROM library_configurations WHERE id=:id"), {"id": cfg_id})
                            session.commit()
                        st.success(f"Configuration template mapping rule '{cfg_code}' deleted.")
                        st.rerun()
        else:
            st.info("No customized setup mappings provisioned yet.")
            
    with admin_tab3:
        st.subheader("System Users Overview")
        user_metrics = admin_get_all_users_metrics()
        if user_metrics:
            display_df = pd.DataFrame(user_metrics).drop(columns=["db_id"])
            st.dataframe(display_df, use_container_width=True, hide_index=True)
            
            st.write("")
            st.caption("⚙️ Quick Actions")
            delete_candidates = [u["Username"] for u in user_metrics if u["Username"].lower() != "admin"]
            
            if delete_candidates:
                target_username = st.selectbox("Select account to remove:", delete_candidates)
                if st.button("🚨 Terminate Account", type="secondary", use_container_width=True):
                    target_id = next(u["db_id"] for u in user_metrics if u["Username"] == target_username)
                    admin_delete_user_and_library(target_id)
                    st.success(f"Successfully purged account: {target_username}")
                    st.rerun()
            else:
                st.info("No external user accounts currently registered.")
        else:
            st.info("No system users found.")
            
    with admin_tab4:
        st.subheader("Global Library Master Logs")
        all_books = admin_get_all_books()
        if all_books:
            for book_row in all_books:
                b_id = book_row["book_id"]
                b_owner = book_row["Owner"]
                b_title = book_row["Title"]
                b_cat = book_row["Category"]
                
                b_col1, b_col2 = st.columns([3, 1])
                with b_col1:
                    st.markdown(f"📖 **{b_title}** | Category: `{b_cat}` | Owner: `{b_owner}`")
                with b_col2:
                    if st.button("Purge From Library", key=f"admin_purge_bk_{b_id}", type="secondary", use_container_width=True):
                        admin_global_delete_book(b_id)
                        st.success(f"Successfully removed '{b_title}' platform-wide.")
                        st.rerun()
        else:
            st.info("No books recorded platform-wide.")
    st.divider()

# -------- GATEWAY OCCUPANCY THRESHOLD VERIFICATION CODES DECK -------- #
if st.session_state.library_config is None:
    st.subheader("🔒 Target Access Verification Required")
    st.info("Please enter your venue configuration access code to open your layout tracking panels.")
    
    with st.form("gateway_verification_code_form"):
        entered_code = st.text_input("Enter Access Code Key").strip()
        remember_code = st.checkbox("Remember this access code")
        submit_gate = st.form_submit_button("Verify & Mount Storage Scope Layout")
        
        if submit_gate:
            is_first_member = False
            match_df = conn.query("SELECT id, library_name, library_type, max_accounts FROM library_configurations WHERE access_code=:ac", params={"ac": entered_code}, ttl=0)
            if not match_df.empty:
                cfg_id = int(match_df.iloc[0]["id"])
                cfg_name = match_df.iloc[0]["library_name"]
                cfg_type = match_df.iloc[0]["library_type"]
                cfg_max = int(match_df.iloc[0]["max_accounts"])
                
                # Query historical database registries tracking occupied allocations
                membership_log_df = conn.query("SELECT user_id FROM library_memberships WHERE config_id=:cid", params={"cid": cfg_id}, ttl=0)
                registered_member_ids = membership_log_df["user_id"].tolist() if not membership_log_df.empty else []
                # Check validation boundaries logic metrics mapping
                grant_token_entry = False
                if st.session_state.user_id in registered_member_ids or is_admin:
                    grant_token_entry = True
                else:
                    # Account claiming a completely new registration seat slot space
                    if len(registered_member_ids) >= cfg_max:
                        if cfg_type == "Singular":
                            st.error("❌ Access Claim Refused. This singular library space has already been activated.")
                        else:
                            st.error(f"❌ Access Claim Refused. This Team container has reached its limit ({cfg_max}/{cfg_max}).")
                    else:
                        # --- TEAM LEADER LOGIC ---
                        # If list is empty, this user is the first member, therefore the Leader
                        is_first_member = (len(registered_member_ids) == 0)
                        
                        grant_token_entry = True
                        current_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        with conn.session as session:
                            session.execute(text("""
                                INSERT INTO library_memberships (config_id, user_id, joined_at, is_leader)
                                VALUES (:cid, :uid, :jat, :leader)
                            """), {
                                "cid": cfg_id, 
                                "uid": st.session_state.user_id, 
                                "jat": current_ts,
                                "leader": is_first_member
                            })
                            session.commit()
                
                if grant_token_entry:
                    st.session_state.library_config = {
                        "name": cfg_name,
                        "access_code": entered_code,
                        "type": cfg_type,
                        "max_accounts": cfg_max
                    }
                    
                    if remember_code:
                        cookie_manager.set(
                            cookie="library_access_code",
                            val=entered_code,
                            expires_at=datetime.now() + pd.Timedelta(days=30)
                        )
                    st.success(f"Access granted! Welcome, {'Team Leader' if is_first_member else 'Member'}.")
                    st.rerun()
            else:
                st.error("Invalid configuration key parameters.")
    st.stop()
# ---------------- RUNTIME CORE APPLICATION PANELS ---------------- #
# Before calling:
match_df = conn.query("SELECT id FROM library_configurations WHERE access_code=:ac", 
                      params={"ac": st.session_state.library_config['access_code']}, ttl=0)
cfg_id = int(match_df.iloc[0]["id"]) if not match_df.empty else None

# Call the updated function
books_list = load_books_from_db(cfg_id, is_admin, st.session_state.user_id)

st.header(f"{dynamic_icon} Workspace: {st.session_state.library_config['name']}")

# Dynamic sidebar actions mapping context parameters allocation rules
with st.sidebar:
    st.divider()
    st.header("Add a Book")
    title = st.text_input("Book title")
    category = st.selectbox("Category", CATEGORIES, key="add_category")
    uploaded_file = st.file_uploader("Upload book photo", type=["png", "jpg", "jpeg"], key="add_photo")

    if st.button("Add Book", use_container_width=True):
        if title.strip() == "":
            st.error("Please enter a book title.")
        else:
            image_bytes = uploaded_file.getvalue() if uploaded_file else None
            image_name = uploaded_file.name if uploaded_file else None
            add_book_to_db(st.session_state.user_id, title.strip(), category, image_bytes, image_name)
            st.success(f"Added: {title}")
            st.rerun()

    if books_list:
        st.divider()
        st.subheader("⚠️ Danger Zone")
        confirm_delete = st.checkbox("I want to clear my entire library")
        if st.button("Delete All Books", type="primary", use_container_width=True, disabled=not confirm_delete):
            delete_all_books_from_db(st.session_state.user_id)
            st.success("All books have been cleared.")
            st.rerun()

# Layout Architecture Dashboards
col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("Your Books")
    if books_list:
        df_display = pd.DataFrame([{"Title": b["title"], "Category": b["category"], "Has Photo": "Yes" if b["image_bytes"] else "No"} for b in books_list])
        st.dataframe(df_display, use_container_width=True, hide_index=True)
    else:
        st.info("No books added yet.")

with col2:
    st.subheader("Category Summary")
    if books_list:
        counts = pd.DataFrame(books_list)["category"].value_counts().reindex(CATEGORIES, fill_value=0)
        st.bar_chart(counts)
    else:
        st.write("Add books to see the summary.")

st.divider()
st.subheader("Book Gallery")

# --- START OF YOUR PERMISSION CHECK ---
# We use a string here instead of text() to avoid the UnhashableParamError
leader_query = "SELECT is_leader FROM library_memberships WHERE user_id = :uid AND config_id = (SELECT id FROM library_configurations WHERE access_code = :ac)"

leader_df = conn.query(leader_query, params={"uid": st.session_state.user_id, "ac": st.session_state.library_config['access_code']}, ttl=0)

is_leader = False
if not leader_df.empty:
    is_leader = leader_df.iloc[0]["is_leader"]
# --- END OF YOUR PERMISSION CHECK ---

if books_list:
    gallery_cols = st.columns(3)
    for i, book in enumerate(books_list):
        with gallery_cols[i % 3]:
            if st.session_state.editing_book_id == book["id"]:
                st.markdown(f"#### 📝 Edit Details")
                with st.form(f"edit_form_{book['id']}", clear_on_submit=True):
                    edit_title = st.text_input("Book Title", value=book["title"])
                    default_idx = CATEGORIES.index(book["category"]) if book["category"] in CATEGORIES else 0
                    edit_category = st.selectbox("Category", CATEGORIES, index=default_idx)
                    edit_file = st.file_uploader("Replace Book Photo (Optional)", type=["png", "jpg", "jpeg"])
                    
                    btn_save, btn_cancel = st.columns(2)
                    with btn_save:
                        save_changes = st.form_submit_button("Save", use_container_width=True)
                    with btn_cancel:
                        cancel_changes = st.form_submit_button("Cancel", use_container_width=True)
                    
                    if save_changes:
                        if edit_title.strip() == "":
                            st.error("Title cannot be blank.")
                        else:
                            img_bytes = edit_file.getvalue() if edit_file else None
                            img_name = edit_file.name if edit_file else None
                            update_book_in_db(book["id"], st.session_state.user_id, edit_title.strip(), edit_category, img_bytes, img_name)
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
                        st.caption("⚠️ [Image Display Error]")
                else:
                    st.write("No photo uploaded.")
    
                action_edit, action_del = st.columns(2)
                if is_leader or is_admin:
                    with action_edit:
                        if st.button(f"📝 Edit", key=f"edit_btn_{book['id']}", use_container_width=True):
                            st.session_state.editing_book_id = book["id"]
                            st.rerun()
                    with action_del:
                        if st.button(f"🗑️ Delete", key=f"del_{book['id']}", use_container_width=True):
                            delete_book_from_db(book["id"], st.session_state.user_id)
                            st.success(f"Deleted '{book['title']}'")
                            st.rerun()
else:
    st.write("Upload some books to display them here.")