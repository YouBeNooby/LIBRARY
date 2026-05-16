import sqlite3
import hashlib
from datetime import datetime
import pandas as pd
import streamlit as st
from PIL import Image
import io

# 1. Page Configuration
st.set_page_config(page_title="Book Classifier", page_icon="📚", layout="wide")

CATEGORIES = ["read one time", "read again", "give away", "read pending"]
DB_NAME = "books_db.sqlite"


# 2. Database Initialization & Security Functions
def make_hashes(password):
    """Hashes a password for secure storage."""
    return hashlib.sha256(str.encode(password)).hexdigest()


def init_db():
    """Creates tables for users and books if they don't exist and ensures an admin exists."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Create Users Table (UPDATED: Added registration_date)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            registration_date TEXT NOT NULL
        )
    """)
    
    # Create Books Table linked to user_id
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

    # --- FIX: AUTOMATIC ADMIN INSURANCE ---
    # The default admin account password is securely set to LeBakri!!
    # Admin is given a baseline old registration date so they always stay as #1
    admin_password = "LeBakri!!" 
    hashed_admin_password = make_hashes(admin_password)
    
    cursor.execute("""
        INSERT OR IGNORE INTO users (username, password, registration_date) 
        VALUES (?, ?, ?)
    """, ("admin", hashed_admin_password, "2000-01-01 00:00:00"))
    conn.commit()
    
    conn.close()


def add_user(username, password):
    """Registers a new user with the current date/time."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        cursor.execute("""
            INSERT INTO users (username, password, registration_date) 
            VALUES (?, ?, ?)
        """, (username, make_hashes(password), current_time))
        conn.commit()
        success = True
    except sqlite3.IntegrityError:
        success = False  # Username already exists
    conn.close()
    return success


def login_user(username, password):
    """Checks credentials and returns user tuple or None."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, username FROM users WHERE username = ? AND password = ?", (username, make_hashes(password)))
    user = cursor.fetchone()
    conn.close()
    return user


def update_user_password(user_id, new_password):
    """Updates the password for a specific user ID securely."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET password = ? WHERE id = ?", (make_hashes(new_password), user_id))
    conn.commit()
    conn.close()


def add_book_to_db(user_id, title, category, image_bytes, image_name):
    """Inserts a new book record tied to a specific user."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO books (user_id, title, category, image_bytes, image_name)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, title, category, image_bytes, image_name))
    conn.commit()
    conn.close()


def delete_book_from_db(book_id, user_id):
    """Deletes a book record ensuring it belongs to the logged-in user."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM books WHERE id = ? AND user_id = ?", (book_id, user_id))
    conn.commit()
    conn.close()


def delete_all_books_from_db(user_id):
    """Deletes all book records belonging to the logged-in user."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM books WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def load_books_from_db(user_id):
    """Fetches books belonging exclusively to the logged-in user."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, category, image_bytes, image_name FROM books WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()

    books = []
    for row in rows:
        books.append({
            "id": row[0],
            "title": row[1],
            "category": row[2],
            "image_bytes": row[3],
            "image_name": row[4]
        })
    return books


# --- ADMIN ONLY DATABASE FUNCTIONS ---
def admin_get_all_users_metrics():
    """
    Fetches all users, orders them by registration date, and calculates a 
    gapless, fluid 'User No.' based on current active sign-ups.
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # ROW_NUMBER() recalculates numbers dynamically 1, 2, 3... based on registration date order
    query = """
        SELECT 
            ROW_NUMBER() OVER (ORDER BY users.registration_date ASC) AS dynamic_no,
            users.id, 
            users.username, 
            COUNT(books.id) AS total_books
        FROM users
        LEFT JOIN books ON users.id = books.user_id
        GROUP BY users.id
        ORDER BY users.registration_date ASC
    """
    cursor.execute(query)
    rows = cursor.fetchall()
    conn.close()
    
    # We pass both the dynamic display number and the hidden database ID back
    return [{"User No.": r[0], "db_id": r[1], "Username": r[2], "Books Tracked": r[3]} for r in rows]


def admin_get_all_books():
    """Fetches every single book record in the system across all users."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    query = """
        SELECT books.id, users.username, books.title, books.category
        FROM books
        JOIN users ON books.user_id = users.id
    """
    cursor.execute(query)
    rows = cursor.fetchall()
    conn.close()
    return [{"Book ID": r[0], "Owner": r[1], "Title": r[2], "Category": r[3]} for r in rows]


def admin_delete_user_and_library(target_user_id):
    """Deletes user record and safely drops all linked collection profiles."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM books WHERE user_id = ?", (target_user_id,))
    cursor.execute("DELETE FROM users WHERE id = ?", (target_user_id,))
    conn.commit()
    conn.close()


# Initialize database structure
init_db()

# Initialize session state variables for authentication tracking
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "user_id" not in st.session_state:
    st.session_state.user_id = None
if "username" not in st.session_state:
    st.session_state.username = None


# 3. Authentication UI Workflow
if not st.session_state.logged_in:
    st.title("📚 Book Classifier")
    st.subheader("Please Login or Register to access your collection")
    
    auth_mode = st.radio("Choose Action", ["Login", "Register"], horizontal=True)
    
    with st.form("auth_form"):
        username = st.text_input("Username").strip()
        password = st.text_input("Password", type="password")
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
                    st.success(f"Welcome back, {username}!")
                    st.rerun()
                else:
                    st.error("Invalid username or password.")
    st.stop()


# 4. Main App Interface (Accessible only when logged in)
is_admin = st.session_state.username.lower() == "admin"
books_list = load_books_from_db(st.session_state.user_id)

st.title("📚 Book Classifier")
st.write(f"Logged in as: **{st.session_state.username}**" + (" *(Administrator)*" if is_admin else ""))

# Sidebar
with st.sidebar:
    st.header("Control Panel")
    if st.button("Log Out", type="primary", use_container_width=True):
        st.session_state.logged_in = False
        st.session_state.user_id = None
        st.session_state.username = None
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
    category = st.selectbox("Category", CATEGORIES)
    uploaded_file = st.file_uploader("Upload book photo", type=["png", "jpg", "jpeg"])

    add_clicked = st.button("Add Book", use_container_width=True)

    if add_clicked:
        if title.strip() == "":
            st.error("Please enter a book title.")
        else:
            image_bytes = None
            image_name = None
            if uploaded_file is not None:
                image_bytes = uploaded_file.getvalue()
                image_name = uploaded_file.name

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


# Main Dashboard Layout
col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("Your Books")
    if books_list:
        df = pd.DataFrame([
            {
                "Title": b["title"],
                "Category": b["category"],
                "Has Photo": "Yes" if b["image_bytes"] else "No"
            } for b in books_list
        ])
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
            st.markdown(f"**{book['title']}**")
            st.caption(book["category"])

            if book["image_bytes"]:
                image = Image.open(io.BytesIO(book["image_bytes"]))
                st.image(image, use_container_width=True)
            else:
                st.write("No photo uploaded.")

            if st.button(f"🗑️ Delete", key=f"del_{book['id']}", use_container_width=True):
                delete_book_from_db(book["id"], st.session_state.user_id)
                st.success(f"Deleted '{book['title']}'")
                st.rerun()
else:
    st.write("Upload some books to display them here.")


# 5. Admin Dashboard Panel (Only renders if username is 'admin')
if is_admin:
    st.block_output = st.empty()
    st.divider()
    st.header("🛠️ Admin Management Dashboard")
    st.caption("This panel is hidden from normal application accounts.")
    
    admin_col1, admin_col2 = st.columns(2)
    
    with admin_col1:
        st.subheader("System Users Overview")
        user_metrics = admin_get_all_users_metrics()
        if user_metrics:
            # We filter out the internal db_id so it remains invisible to the table layout
            display_df = pd.DataFrame(user_metrics).drop(columns=["db_id"])
            st.dataframe(display_df, use_container_width=True, hide_index=True)
            
            st.write("")
            st.caption("⚙️ Quick Actions")
            delete_candidates = [u["Username"] for u in user_metrics if u["Username"].lower() != "admin"]
            
            if delete_candidates:
                target_username = st.selectbox("Select account to remove:", delete_candidates)
                if st.button("🚨 Terminate Account", type="secondary", use_container_width=True):
                    # Fetch internal db_id securely behind the scenes to process deletion
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