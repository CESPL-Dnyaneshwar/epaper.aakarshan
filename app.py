from flask import Flask, render_template, request, send_file,  redirect, url_for, session, flash
import os, requests, shutil, io
from datetime import date, datetime, timedelta
import fitz  # PyMuPDF
from functools import wraps
from PIL import Image
import json
from supabase import create_client

app = Flask(__name__)
app.secret_key = "kya-developer-benega-re-tu-!!!"

# Supabase Configuration
SUPABASE_URL = "https://jytcjobmredpfekmlwvu.supabase.co"
SUPABASE_SERVICE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imp5dGNqb2JtcmVkcGZla21sd3Z1Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2Nzg2NTk4NywiZXhwIjoyMDgzNDQxOTg3fQ.78UiL7YYoiDc3k5a4bg1VrCljqUvHykKx22knmQkyyA'

#os.getenv("SUPABASE_SERVICE_KEY")

BUCKET_NAME = "enewspaper"
ARTICLES_FILE = "articles.json"
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

CACHE_FOLDER = "pdf_cache"
VISIT_FILE = "visit_counts.json"


# Create necessary folders
os.makedirs(CACHE_FOLDER, exist_ok=True)
os.makedirs("static", exist_ok=True)
os.makedirs(os.path.join("static", "pages"), exist_ok=True)


# Initialize visit_counts.json if it doesn't exist
if not os.path.exists(VISIT_FILE):
    with open(VISIT_FILE, "w") as f:
        json.dump({}, f)
    print(f"Created {VISIT_FILE}")

def load_visits():
    """Load visit counts from JSON file"""
    try:
        if not os.path.exists(VISIT_FILE):
            return {}
        with open(VISIT_FILE, "r") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"Error loading visits: {e}")
        return {}

@app.route("/admin/sync-articles", methods=["POST"])
def sync_articles_to_supabase():
    try:
        with open(ARTICLES_FILE, "rb") as f:
            payload = f.read()

        supabase.storage.from_(BUCKET_NAME).upload(
            ARTICLES_FILE,
            payload,
            {"content-type": "application/json", "x-upsert": "true"}
        )

        print("☁️ Supabase backup updated")
        return {"status": "ok"}

    except Exception as e:
        print("❌ Supabase backup failed:", e)
        return {"status": "error"}, 500
        
def load_articles():
    try:
        with open(ARTICLES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_articles(data):
    with open(ARTICLES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print("✅ articles.json saved locally")
        
def get_next_article_id(date):
    data = load_articles()
    day = data.get(date, {})
    if not day:
        return "1"
    return str(max(map(int, day.keys())) + 1)

def restore_articles_from_supabase():
    try:
        res = supabase.storage.from_(BUCKET_NAME).download(ARTICLES_FILE)
        with open(ARTICLES_FILE, "wb") as f:
            f.write(res)
        print("☁️ articles.json restored from Supabase")
    except:
        print("⚠️ No Supabase backup found, using local file")
        
def save_visits(data):
    """Save visit counts to JSON file"""
    try:
        with open(VISIT_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Error saving visits: {e}")

def increase_visit(pdf_filename):
    """Increase visit count for a PDF"""
    try:
        visits = load_visits()
        visits[pdf_filename] = visits.get(pdf_filename, 0) + 1
        save_visits(visits)
        print(f"Visit count for {pdf_filename}: {visits[pdf_filename]}")
    except Exception as e:
        print(f"Error increasing visit: {e}")

def verify_user(username, password):
    """Verify user credentials against Supabase User table"""
    try:
        if not username or not password:
            return False, "Username and password are required"

        url = f"{SUPABASE_URL}/rest/v1/User"
        headers = {
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        params = {
            "username": f"eq.{username}",
            "select": "username,password"
        }

        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
        except Exception as e:
            print(f"Auth request failed: {e}")
            return False, f"Auth request error: {e}"

        if response.status_code != 200:
            print(f"Auth DB error: {response.status_code} - {response.text}")
            return False, f"Database error: {response.status_code}"

        users = response.json()
        if not users:
            return False, "User not found"

        user = users[0]
        stored_password = user.get('password')
        if stored_password is None:
            return False, "Password field not found in DB"

        if stored_password == password:
            return True, "Login successful"
        else:
            return False, "Invalid password"

    except Exception as e:
        print(f"Auth error (unexpected): {e}")
        return False, str(e)

def get_pdf_url(pdf_date):
    """Generate Supabase URL for a given date"""
    filename = f"{pdf_date.strftime('%Y-%m-%d')}.pdf"
    return f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET_NAME}/{filename}"

def check_pdf_exists(pdf_url):
    """Check if PDF exists at the URL"""
    try:
        response = requests.head(pdf_url, timeout=5)
        return response.status_code == 200
    except:
        return False

def cleanup_cache(current_pdf=None):
    """
    Keep only:
      - Current PDF being viewed (PROTECTED)
            - Top 5 latest PDFs by date
        Remove everything else ONLY if cache exceeds 6 files
    Also cleanup corresponding image folders
    """
    try:
        visits = load_visits()

        # All PDFs in cache
        cached_files = [
            f for f in os.listdir(CACHE_FOLDER)
            if f.endswith(".pdf")
        ]

        # Only cleanup if we have more than 6 files
        if len(cached_files) <= 6:
            print(f"Cache has {len(cached_files)} files, no cleanup needed")
            return

        print(f"Cache has {len(cached_files)} files, starting cleanup...")

        # Keep the five latest PDFs, plus the current PDF if it is not already included.
        try:
            latest_files = sorted(
                cached_files,
                key=lambda x: datetime.strptime(x.replace(".pdf", ""), "%Y-%m-%d"),
                reverse=True
            )[:5]
        except Exception as e:
            print(f"Error sorting by date: {e}")
            latest_files = []

        keep_files = set(latest_files)
        
        # ALWAYS protect current PDF being viewed
        if current_pdf and current_pdf.endswith('.pdf'):
            keep_files.add(current_pdf)
            print(f"Protecting current PDF: {current_pdf}")

        print(f"Will keep {len(keep_files)} files: {keep_files}")

        # Delete PDFs and corresponding image folders
        deleted_count = 0
        for f in cached_files:
            if f not in keep_files:
                try:
                    # Delete PDF
                    file_path = os.path.join(CACHE_FOLDER, f)
                    os.remove(file_path)
                    
                    # Delete corresponding image folder
                    date_str = f.replace(".pdf", "")
                    img_folder = os.path.join("static", "pages", date_str)
                    if os.path.exists(img_folder):
                        shutil.rmtree(img_folder)
                        print(f"Deleted image folder: {img_folder}")
                    
                    deleted_count += 1
                    print(f"Deleted old cache: {f}")
                except Exception as e:
                    print(f"Error deleting {f}: {e}")

        print(f"Cache cleanup complete: kept {len(keep_files)} files, deleted {deleted_count} files")
    except Exception as e:
        print(f"Error in cleanup_cache: {e}")

def get_available_dates(start_date, end_date):
    """Get list of dates that have PDFs available"""
    available = []
    current = start_date
    
    while current <= end_date:
        pdf_url = get_pdf_url(current)
        if check_pdf_exists(pdf_url):
            available.append(current)
            print(f"Found PDF for: {current}")
        current += timedelta(days=1)
    
    return available

def download_pdf(pdf_url, cache_path):
    """Download PDF from Supabase and cache it locally"""
    try:
        print(f"Downloading from: {pdf_url}")
        response = requests.get(pdf_url, stream=True, timeout=20)
        response.raise_for_status()
        
        with open(cache_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        print(f"Downloaded successfully to: {cache_path}")
        return True
    except Exception as e:
        print(f"Error downloading PDF: {e}")
        return False

def upload_to_supabase(file_data, filename):
    """Upload PDF to Supabase Storage"""
    try:
        check_url = f"{SUPABASE_URL}/storage/v1/object/{BUCKET_NAME}/{filename}"
        headers = {
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "apikey": SUPABASE_SERVICE_KEY
        }
        
        # Try to delete existing file
        requests.delete(check_url, headers=headers)
        
        # Upload new file
        upload_url = f"{SUPABASE_URL}/storage/v1/object/{BUCKET_NAME}/{filename}"
        upload_headers = {
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "apikey": SUPABASE_SERVICE_KEY,
            "Content-Type": "application/pdf"
        }
        
        response = requests.post(upload_url, headers=upload_headers, data=file_data)
        
        if response.status_code in [200, 201]:
            print(f"Successfully uploaded: {filename}")
            return True, "File uploaded successfully"
        else:
            print(f"Upload failed: {response.status_code} - {response.text}")
            return False, f"Upload failed: {response.text}"
            
    except Exception as e:
        print(f"Error uploading to Supabase: {e}")
        return False, str(e)

def delete_from_supabase(filename):
    """Delete PDF from Supabase Storage"""
    try:
        url = f"{SUPABASE_URL}/storage/v1/object/{BUCKET_NAME}/{filename}"
        headers = {
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "apikey": SUPABASE_SERVICE_KEY
        }
        
        response = requests.delete(url, headers=headers)
        
        if response.status_code == 200:
            return True, "File deleted successfully"
        else:
            return False, f"Delete failed: {response.text}"
            
    except Exception as e:
        return False, str(e)

def pdf_to_images(pdf_path, output_folder):
    """Convert PDF to JPG images using PyMuPDF"""
    doc = fitz.open(pdf_path)

    for page_index, page in enumerate(doc, start=1):
        pix = page.get_pixmap(dpi=150)
        img_bytes = pix.tobytes("png")

        img = Image.open(io.BytesIO(img_bytes))
        img.save(os.path.join(output_folder, f"page_{page_index}.jpg"), "JPEG")

    doc.close()

def generate_share_thumbnail(pages_folder, date_str):
    """Generate a landscape 2:1 thumbnail of page 1 for WhatsApp sharing"""
    thumb_path = os.path.join("static", date_str, f"share_{date_str}.jpg")
    
    # Already generated, skip
    if os.path.exists(thumb_path):
        return f"/static/{date_str}/share_{date_str}.jpg"
    
    page1_path = os.path.join(pages_folder, "page_1.jpg")
    if not os.path.exists(page1_path):
        return None

    try:
        os.makedirs(os.path.join("static", date_str), exist_ok=True)
        img = Image.open(page1_path)
        w, h = img.size
        # Crop top 50% of page → landscape 2:1 ratio
        crop_height = w // 2
        cropped = img.crop((0, 0, w, crop_height))
        cropped = cropped.resize((876, 438), Image.LANCZOS)
        cropped.save(thumb_path, "JPEG", quality=85)
        print(f"✅ Share thumbnail generated: {thumb_path}")
        return f"/static/{date_str}/share_{date_str}.jpg"
    except Exception as e:
        print(f"❌ Error generating share thumbnail: {e}")
        return None
    
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

restore_articles_from_supabase()
    
# Cache available dates
print("Scanning for available newspapers...")
end_date = date.today()
start_date = end_date - timedelta(days=10)
available_dates = get_available_dates(start_date, end_date)
print(f"Found {len(available_dates)} newspapers")

# ============= PUBLIC ROUTES =============
@app.route("/", methods=["GET"])
def home():
    global available_dates

    # ---------------- LOAD AVAILABLE DATES ----------------
    if not available_dates:
        end_date = date.today()
        start_date = end_date - timedelta(days=10)
        available_dates = get_available_dates(start_date, end_date)

    if not available_dates:
        return render_template(
            "index.html",
            dates=[],
            selected_date=None,
            error_message="No newspapers available.",
            total_pages=0,
            page_no=0,
            img_path=None,
            pdf_name=None,
            thumbs=[],
            page_articles=[]
        )

    # ---------------- GET SELECTED DATE ----------------
    date_param = request.args.get("date")

    if date_param:
        try:
            selected_date = datetime.strptime(date_param, "%Y-%m-%d").date()
        except:
            selected_date = max(available_dates)
    else:
        selected_date = max(available_dates)

    pdf_filename = f"{selected_date}.pdf"
    pdf_url = get_pdf_url(selected_date)

    # ---------------- VALIDATE DATE ----------------
    if selected_date not in available_dates:
        if check_pdf_exists(pdf_url):
            available_dates.append(selected_date)
            available_dates.sort()
        else:
            return render_template(
                "index.html",
                dates=sorted(available_dates, reverse=True),
                selected_date=selected_date,
                error_message="Paper not uploaded yet.",
                total_pages=0,
                page_no=0,
                img_path=None,
                pdf_name=None,
                thumbs=[],
                page_articles=[]
            )

    # ---------------- CACHE PDF LOCALLY ----------------
    cache_path = os.path.join(CACHE_FOLDER, pdf_filename)

    if not os.path.exists(cache_path):
        if not download_pdf(pdf_url, cache_path):
            return render_template(
                "index.html",
                dates=sorted(available_dates, reverse=True),
                selected_date=selected_date,
                error_message="Download failed.",
                total_pages=0,
                page_no=0,
                img_path=None,
                pdf_name=None,
                thumbs=[],
                page_articles=[]
            )

    increase_visit(pdf_filename)
    cleanup_cache(current_pdf=pdf_filename)

    # ---------------- PAGE IMAGE FOLDER ----------------
    pages_folder = os.path.join("static", "pages", selected_date.strftime("%Y-%m-%d"))
    os.makedirs(pages_folder, exist_ok=True)

    existing_pages = sorted(
        [f for f in os.listdir(pages_folder) if f.endswith(".jpg")]
    )

    if not existing_pages:
        pdf_to_images(cache_path, pages_folder)
        existing_pages = sorted(
            [f for f in os.listdir(pages_folder) if f.endswith(".jpg")]
        )

    total_pages = len(existing_pages)

    # ---------------- PAGE SELECTION ----------------
    page_no = int(request.args.get("page", 1))
    page_no = max(1, min(page_no, total_pages))

    # ---------------- MAIN VIEW IMAGE ----------------
    img_path = f"/static/pages/{selected_date.strftime('%Y-%m-%d')}/{existing_pages[page_no-1]}"
    # Generate share thumbnail for WhatsApp
    share_thumb = generate_share_thumbnail(
        pages_folder,
        selected_date.strftime('%Y-%m-%d')
    )
    # ---------------- THUMBS ----------------
    thumbs_folder = os.path.join(pages_folder, "thumbs")
    os.makedirs(thumbs_folder, exist_ok=True)

    for i, filename in enumerate(existing_pages, start=1):
        thumb_path = os.path.join(thumbs_folder, f"page_{i}.jpg")
        if not os.path.exists(thumb_path):
            img = Image.open(os.path.join(pages_folder, filename))
            img.thumbnail((180, 260))
            img.save(thumb_path, "JPEG")

    thumbs = [
        f"/static/pages/{selected_date.strftime('%Y-%m-%d')}/thumbs/page_{i}.jpg"
        for i in range(1, total_pages + 1)
    ]

    # =====================================================
    # 🔥 NEW PART: LOAD ARTICLE OVERLAYS FOR THIS PAGE
    # =====================================================
    page_articles = []

    articles = load_articles()
    date_key = selected_date.strftime("%Y-%m-%d")

    if date_key in articles:
        for article_no, a in articles[date_key].items():
            if a.get("page") == page_no:
                page_articles.append({
                    "article_no": article_no,
                    "x": a["x"],
                    "y": a["y"],
                    "w": a["w"],
                    "h": a["h"]
                })

    # ---------------- RENDER ----------------
    return render_template(
        "index.html",
        dates=sorted(available_dates, reverse=True),
        selected_date=selected_date,
        total_pages=total_pages,
        page_no=page_no,
        img_path=img_path,
        share_thumb=share_thumb, 
        pdf_name=pdf_filename,
        thumbs=thumbs,
        page_articles=page_articles,
        error_message=None
    )


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if 'logged_in' in session:
        return redirect(url_for('admin_dashboard'))
    
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        
        success, message = verify_user(username, password)
        
        if success:
            session['logged_in'] = True
            session['username'] = username
            flash("Login successful! Welcome back.", "success")
            return redirect(url_for('admin_dashboard'))
        else:
            flash(f"Login failed: {message}", "error")
    
    return render_template("admin_login.html")

@app.route("/admin/logout")
def admin_logout():
    session.pop('logged_in', None)
    flash("Logged out successfully!", "success")
    return redirect(url_for('admin_login'))

@app.route("/admin")
@login_required
def admin_dashboard():
    global available_dates
    return render_template(
        "admin_dashboard.html",
        newspapers=sorted(available_dates, reverse=True),
        today=date.today().strftime('%Y-%m-%d')
    )

@app.route("/admin/upload", methods=["POST"])
@login_required
def admin_upload():
    if 'pdf_file' not in request.files:
        flash("No file selected!", "error")
        return redirect(url_for('admin_dashboard'))
    
    file = request.files['pdf_file']
    upload_date = request.form.get('upload_date')
    
    if file.filename == '':
        flash("No file selected!", "error")
        return redirect(url_for('admin_dashboard'))
    
    if not upload_date:
        flash("Please select a date!", "error")
        return redirect(url_for('admin_dashboard'))
    
    if not file.filename.endswith('.pdf'):
        flash("Only PDF files are allowed!", "error")
        return redirect(url_for('admin_dashboard'))
    
    try:
        file_date = datetime.strptime(upload_date, '%Y-%m-%d').date()
        filename = f"{file_date.strftime('%Y-%m-%d')}.pdf"
        
        file_data = file.read()
        
        success, message = upload_to_supabase(file_data, filename)
        
        if success:
            # Clear cache and images for this date
            cache_path = os.path.join(CACHE_FOLDER, filename)
            if os.path.exists(cache_path):
                os.remove(cache_path)
            
            img_folder = os.path.join("static", "pages", file_date.strftime('%Y-%m-%d'))
            if os.path.exists(img_folder):
                shutil.rmtree(img_folder)
            
            # Refresh available dates
            global available_dates
            if file_date not in available_dates:
                available_dates.append(file_date)
                available_dates.sort()
            
            flash(f"Newspaper for {file_date.strftime('%B %d, %Y')} uploaded successfully!", "success")
        else:
            flash(f"Upload failed: {message}", "error")
            
    except Exception as e:
        flash(f"Error: {str(e)}", "error")
    
    return redirect(url_for('admin_dashboard'))

@app.route("/admin/save-article", methods=["POST"])
@login_required
def save_article():
    data = load_articles()

    date = request.form["date"]
    title = request.form["title"]
    page_raw = request.form.get("page")
    page = int(page_raw) if page_raw and page_raw.isdigit() else 1
    article = {
        "title": title,
        "page": page,
        "x": float(request.form["x"]),
        "y": float(request.form["y"]),
        "w": float(request.form["w"]),
        "h": float(request.form["h"])
    }

    if date not in data:
        data[date] = {}

    article_no = get_next_article_id(date)
    data[date][article_no] = article

    save_articles(data)

    link = f"/article/{date}/{article_no}"
    return link

@app.route("/admin/delete-article/<date>/<article_no>", methods=["POST"])
@login_required
def delete_article(date, article_no):
    data = load_articles()
    if date in data and article_no in data[date]:
        del data[date][article_no]
        save_articles(data)
    return "OK"


@app.route("/admin/delete/<date_str>", methods=["POST"])
@login_required
def admin_delete(date_str):
    try:
        file_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        filename = f"{file_date.strftime('%Y-%m-%d')}.pdf"
        
        success, message = delete_from_supabase(filename)
        
        if success:
            # Clear cache
            cache_path = os.path.join(CACHE_FOLDER, filename)
            if os.path.exists(cache_path):
                os.remove(cache_path)
            
            # Clear images
            img_folder = os.path.join("static", "pages", file_date.strftime('%Y-%m-%d'))
            if os.path.exists(img_folder):
                shutil.rmtree(img_folder)
            
            # Remove from available dates
            global available_dates
            if file_date in available_dates:
                available_dates.remove(file_date)
            
            flash(f"Newspaper for {file_date.strftime('%B %d, %Y')} deleted successfully!", "success")
        else:
            flash(f"Delete failed: {message}", "error")
            
    except Exception as e:
        flash(f"Error: {str(e)}", "error")
    
    return redirect(url_for('admin_dashboard'))

@app.route("/download/<date_str>")
def download(date_str):
    try:
        file_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        pdf_filename = f"{file_date.strftime('%Y-%m-%d')}.pdf"
        cache_path = os.path.join(CACHE_FOLDER, pdf_filename)
        
        # Download if not cached
        if not os.path.exists(cache_path):
            pdf_url = get_pdf_url(file_date)
            download_pdf(pdf_url, cache_path)
        
        return send_file(cache_path, as_attachment=True, download_name=pdf_filename)
    except Exception as e:
        print(f"Download error: {e}")
        return "Download failed", 500

@app.route("/refresh")
def refresh():
    """Manually refresh the date list"""
    global available_dates
    end_date = date.today()
    start_date = end_date - timedelta(days=10)
    available_dates = get_available_dates(start_date, end_date)
    return {"success": True, "dates": len(available_dates)}

@app.route("/check/<date_str>")
def check_date(date_str):
    """Check if a specific date is available"""
    try:
        check_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        pdf_url = get_pdf_url(check_date)
        exists = check_pdf_exists(pdf_url)
        return {"date": date_str, "available": exists, "url": pdf_url}
    except:
        return {"error": "Invalid date format"}, 400

@app.route("/admin/manage/<date>")
@login_required
def admin_manage(date):
    page = int(request.args.get("page", 1))
    page_image = f"/static/pages/{date}/page_{page}.jpg"

    data = load_articles()
    articles = data.get(date, {})

    return render_template(
        "admin_manage.html",
        date=date,
        page=page,
        page_image=page_image,
        articles=articles
    )

@app.route("/article/<date>/<article_no>")
def view_article_page(date, article_no):
    data = load_articles()
    article = data[date][article_no]

    return render_template(
        "article_view.html",
        article=article,
        date=date,
        article_no=article_no
    )

@app.route("/article-image/<date>/<article_no>")
def article_image(date, article_no):
    data = load_articles()

    if date not in data or article_no not in data[date]:
        return "Image not found", 404

    article = data[date][article_no]

    page = article["page"]
    page_image = f"static/pages/{date}/page_{page}.jpg"

    if not os.path.exists(page_image):
        return "Page image missing", 404

    img = Image.open(page_image)

    width, height = img.size

    left = int(article["x"] / 100 * width)
    top = int(article["y"] / 100 * height)
    right = left + int(article["w"] / 100 * width)
    bottom = top + int(article["h"] / 100 * height)

    cropped = img.crop((left, top, right, bottom))

    buf = io.BytesIO()
    cropped.save(buf, format="JPEG", quality=90)
    buf.seek(0)

    return send_file(buf, mimetype="image/jpeg")

@app.route("/about")
def about():
    return render_template("about.html")

@app.route("/contact")
def contact():
    return render_template("contact.html")

@app.route("/privacy")
def privacy():
    return render_template("privacy.html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)