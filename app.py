import hashlib
from datetime import datetime
import sqlite3
import pandas as pd
import streamlit as st
from PIL import Image
import io

# 1. Page Configuration
st.set_page_config(page_title="Book Library", page_icon="📚", layout="wide")

CATEGORIES = [
    "Read pending",
    "Reading in progress",
    "Already read", 
    "Read again", 
    "Give away", 
    "Wishlist"
]

# 2. Local SQLite Core Initialization Functions
def get_db_connection():
    conn = sqlite3.connect("library.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def make_hashes(password):
    return hashlib.sha256(str.encode(password)).hexdigest()


def init_db():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                registration_date TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                image_bytes BLOB,
                image_name TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        conn.commit()
        
        # Hardcoded Admin Account Insurance
        hashed_admin_password = make_hashes("LeBakri!!")
        try:
            cursor.execute("""
                INSERT INTO users (username, password, registration_date) 
                VALUES (?, ?, ?)
            """, ("admin", hashed_admin_password, "2000-01-01 00:00:00"))
            conn.commit()
        except sqlite3.IntegrityError:
            pass


def add_user(username, password):
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO users (username, password, registration_date) 
                VALUES (?, ?, ?)
            """, (username, make_hashes(password), current_time))
            conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def login_user(username, password):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, username FROM users WHERE username = ? AND password = ?", 
                       (username, make_hashes(password)))
        user = cursor.fetchone()
        return (user["id"], user["username"]) if user else None


def get_user_by_id(user_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, username FROM users WHERE id = ?", (user_id,))
        user = cursor.fetchone()
        return (user["id"], user["username"]) if user else None


def update_user_password(user_id, new_password):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET password = ? WHERE id = ?", (make_hashes(new_password), user_id))
        conn.commit()


def add_book_to_db(user_id, title, category, image_bytes, image_name):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO books (user_id, title, category, image_bytes, image_name)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, title, category, image_bytes, image_name))
        conn.commit()


def update_book_in_db(book_id, user_id, title, category, image_bytes=None, image_name=None):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if image_bytes:
            cursor.execute("""
                UPDATE books 
                SET title = ?, category = ?, image_bytes = ?, image_name = ?
                WHERE id = ? AND user_id = ?
            """, (title, category, image_bytes, image_name, book_id, user_id))
        else:
            cursor.execute("""
                UPDATE books 
                SET title = ?, category = ?
                WHERE id = ? AND user_id = ?
            """, (title, category, book_id, user_id))
        conn.commit()


def delete_book_from_db(book_id, user_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM books WHERE id = ? AND user_id = ?", (book_id, user_id))
        conn.commit()


def delete_all_books_from_db(user_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM books WHERE user_id = ?", (user_id,))
        conn.commit()


def load_books_from_db(user_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, title, category, image_bytes, image_name FROM books WHERE user_id = ? ORDER BY id ASC", (user_id,))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


# --- ADMIN PIPELINE FUNCTIONS ---
def admin_get_all_users_metrics():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        query = """
            SELECT 
                (ROW_NUMBER() OVER (ORDER BY users.registration_date ASC)) AS "User No.",
                users.id AS db_id, 
                users.username AS "Username", 
                COUNT(books.id) AS "Books Tracked"
            FROM users
            LEFT JOIN books ON users.id = books.user_id
            GROUP BY users.id
            ORDER BY users.registration_date ASC
        """
        cursor.execute(query)
        return [dict(row) for row in cursor.fetchall()]


def admin_get_all_books():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        query = """
            SELECT 
                (ROW_NUMBER() OVER (ORDER BY books.id ASC)) AS "Book No.",
                users.username AS "Owner", 
                books.title AS "Title", 
                books.category AS "Category"
            FROM books
            JOIN users ON books.user_id = users.id
            ORDER BY books.id ASC
        """
        cursor.execute(query)
        return [dict(row) for row in cursor.fetchall()]


def admin_delete_user_and_library(target_user_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM books WHERE user_id = ?", (target_user_id,))
        cursor.execute("DELETE FROM users WHERE id = ?", (target_user_id,))
        conn.commit()


# Initialize Local SQLite Engine Environment
init_db()

# --- NATIVE IMMUTABLE URL RECOVERY PARSER ---
if "uid" in st.query_params:
    target_uid = int(st.query_params["uid"])
    user_record = get_user_by_id(target_uid)
    if user_record:
        st.session_state.logged_in = True
        st.session_state.user_id = user_record[0]
        st.session_state.username = user_record[1]

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "user_id" not in st.session_state:
    st.session_state.user_id = None
if "username" not in st.session_state:
    st.session_state.username = None
if "editing_book_id" not in st.session_state:
    st.session_state.editing_book_id = None


# 4. Authentication UI Workflow
if not st.session_state.logged_in:
    st.title("📚 Book Library")
    st.subheader("Please Login or Register to access your collection")
    
    auth_mode = st.radio("Choose Action", ["Login", "Register"], horizontal=True)
    
    with st.form("auth_form"):
        username = st.text_input("Username", key=f"user_{auth_mode}").strip()
        password = st.text_input("Password", type="password", key=f"pass_{auth_mode}")
        submit_auth = st.form_submit_button(auth_mode)
        
        if submit_auth:
            if not username or not password:
                st.error("Please fill in all fields.")
            elif auth_mode == "Register":
                if add_user(username, password):
                    st.success("Registration successful! You can now switch to Login.")
                else:
                    st.error("Username already taken. Please pick another.")
            elif auth_mode == "Login":
                user_record = login_user(username, password)
                if user_record:
                    st.session_state.logged_in = True
                    st.session_state.user_id = user_record[0]
                    st.session_state.username = user_record[1]
                    
                    st.query_params["uid"] = str(user_record[0])
                    st.rerun()
                else:
                    st.error("Invalid username or password.")
    st.stop()


# 5. Main App Interface (Accessible only when logged in)
is_admin = st.session_state.username.lower() == "admin"
books_list = load_books_from_db(st.session_state.user_id)

st.title("📚 Book Library")
st.write(f"Logged in as: **{st.session_state.username}**" + (" *(Administrator)*" if is_admin else ""))

# Sidebar Panels
with st.sidebar:
    st.header("Control Panel")
    if st.button("Log Out", type="primary", use_container_width=True):
        st.session_state.logged_in = False
        st.session_state.user_id = None
        st.session_state.username = None
        st.session_state.editing_book_id = None
        st.query_params.clear()
        st.rerun()
        
    with st.expander("👤 Account Security"):
        st.subheader("Change Password")
        with st.form("change_password_form", clear_on_submit=True):
            new_password = st.text_input("New Password", type="password")
            confirm_password = st.text_input("Confirm New Password", type="password")
            submit_change = st.form_submit_button("Update Password", use_container_width=True)
            
            if submit_change:
                if not new_password or not confirm_password:
                    st.error("Password fields cannot be blank.")
                elif new_password != confirm_password:
                    st.error("Passwords do not match.")
                else:
                    update_user_password(st.session_state.user_id, new_password)
                    st.success("Password updated successfully!")
        
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
        df = pd.DataFrame([{"Title": b["title"], "Category": b["category"], "Has Photo": "Yes" if b["image_bytes"] else "No"} for b in books_list])
        st.dataframe(df, use_container_width=True, hide_index=True)
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
                st.caption(book["category"])
                if book["image_bytes"]:
                    try:
                        st.image(Image.open(io.BytesIO(book["image_bytes"])), use_container_width=True)
                    except Exception:
                        st.caption("⚠️ [Image Display Error]")
                else:
                    st.write("No photo uploaded.")

                action_edit, action_del = st.columns(2)
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


# 6. Admin Dashboard Panel
if is_admin:
    st.divider()
    st.header("🛠️ Admin Management Dashboard")
    st.caption("This panel is hidden from normal application accounts.")
    
    admin_col1, admin_col2 = st.columns(2)
    
    with admin_col1:
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
            
    with admin_col2:
        st.subheader("Global Library Master Logs")
        all_books = admin_get_all_books()
        if all_books:
            st.dataframe(pd.DataFrame(all_books), use_container_width=True, hide_index=True)
        else:
            st.info("No books recorded platform-wide.")