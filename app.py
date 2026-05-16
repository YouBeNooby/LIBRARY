import sqlite3
import hashlib
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
    """Creates tables for users and books if they don't exist."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Create Users Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
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
    conn.close()


def add_user(username, password):
    """Registers a new user."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, make_hashes(password)))
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
    """Fetches all users and maps how many books they have uploaded."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    query = """
        SELECT users.id, users.username, COUNT(books.id) AS total_books
        FROM users
        LEFT JOIN books ON users.id = books.user_id
        GROUP BY users.id
    """
    cursor.execute(query)
    rows = cursor.fetchall()
    conn.close()
    return [{"User ID": r[0], "Username": r[1], "Books Tracked": r[2]} for r in rows]


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
    st.stop()  # Stop app execution here if user is not authenticated


# 4. Main App Interface (Accessible only when logged in)
is_admin = st.session_state.username.lower() == "admin"
books_list = load_books_from_db(st.session_state.user_id)

st.title("📚 Book Classifier")
st.write(f"Logged in as: **{st.session_state.username}**" + (" *(Administrator)*" if is_admin else ""))

# Sidebar for managing books, profile security, and logging out
with st.sidebar:
    st.header("Control Panel")
    if st.button("Log Out", type="primary", use_container_width=True):
        st.session_state.logged_in = False
        st.session_state.user_id = None
        st.session_state.username = None
        st.rerun()
        
    # Change Password Section
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

    # Danger Zone for Mass Deletion
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

# Display personalized Gallery with Delete Feature
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

            # Unique key utilizes database entry ID to safely isolate actions
            if st.button(f"🗑️ Delete", key=f"del_{book['id']}", use_container_width=True):
                delete_book_from_db(book["id"], st.session_state.user_id)
                st.success(f"Deleted '{book['title']}'")
                st.rerun()
else:
    st.write("Upload some books to display them here.")


# 5. Admin Dashboard Panel (Only renders if username is 'admin')
if is_admin:
    st.divider()
    st.header("🛠️ Admin Management Dashboard")
    st.caption("This panel is hidden from normal application accounts.")
    
    admin_col1, admin_col2 = st.columns(2)
    
    with admin_col1:
        st.subheader("System Users Overview")
        user_metrics = admin_get_all_users_metrics()
        
        if user_metrics:
            # We construct a functional layout loop instead of just a static dataframe
            # so we can easily bind clean action buttons next to individual profiles.
            for u in user_metrics:
                # Protect the master 'admin' profile row from removal layout
                if u["Username"].lower() == "admin":
                    st.markdown(f"👤 **{u['Username']}** *(System Owner)* — {u['Books Tracked']} books tracking")
                    continue
                
                u_row1, u_row2 = st.columns([3, 2])
                with u_row1:
                    st.markdown(f"👤 **{u['Username']}** (ID: {u['User ID']})  \n📚 *Books:* {u['Books Tracked']}")
                with u_row2:
                    if st.button("⚠️ Delete User", key=f"adm_del_u_{u['User ID']}", use_container_width=True):
                        admin_delete_user_and_library(u["User ID"])
                        st.success(f"Purged profile '{u['Username']}' and matching collections.")
                        st.rerun()
                st.write("---")
        else:
            st.info("No system users found.")
            
    with admin_col2:
        st.subheader("Global Library Master Logs")
        all_books = admin_get_all_books()
        if all_books:
            st.dataframe(pd.DataFrame(all_books), use_container_width=True, hide_index=True)
        else:
            st.info("No books recorded platform-wide.")