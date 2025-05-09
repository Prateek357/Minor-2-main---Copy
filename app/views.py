import json
import os
import requests
from flask import render_template, redirect, request, send_file, flash, url_for
from app import file_encryptor
from werkzeug.utils import secure_filename
from flask_login import login_user, logout_user, current_user, login_required
from app import app, bcrypt, login_manager, users
from timeit import default_timer as timer
from bson.objectid import ObjectId

class User:
    def __init__(self, user_data):
        self.id = str(user_data['_id'])
        self.username = user_data['username']
        self.email = user_data['email']
        self.password = user_data['password']
        self.is_master = user_data.get('is_master', False)

    @staticmethod
    def get(user_id):
        user_data = users.find_one({'_id': ObjectId(user_id)})
        return User(user_data) if user_data else None

    @staticmethod
    def get_by_username(username):
        user_data = users.find_one({'username': username})
        return User(user_data) if user_data else None

    def is_authenticated(self):
        return True

    def is_active(self):
        return True

    def is_anonymous(self):
        return False

    def get_id(self):
        return self.id

@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id)

@app.route('/master/login', methods=['POST'])
def master_login():
    """Special master login endpoint"""
    from config import Config
    data = request.json
    if (data.get('username') == Config.MASTER_USERNAME and
        data.get('password') == Config.MASTER_PASSWORD and
        data.get('secret') == Config.MASTER_SECRET):
        user_data = users.find_one({'username': Config.MASTER_USERNAME})
        if user_data:
            user = User(user_data)
            login_user(user)
            return jsonify({'status': 'success'})
    return jsonify({'status': 'unauthorized'}), 401

# Stores all the post transaction in the node
request_tx = []
#store filename
files = {}
#destiantion for upload files
import os
UPLOAD_FOLDER = os.path.abspath("app/static/Uploads")
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
# store  address
ADDR = "http://127.0.0.1:8800"


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user_data = users.find_one({'username': username})
        
        if user_data and bcrypt.check_password_hash(user_data['password'], password):
            user = User(user_data)
            login_user(user)
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('index'))
        else:
            flash('Login failed. Check username and password', 'danger')
    
    return render_template('new_login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
        
        if users.find_one({'username': username}):
            flash('Username already exists', 'danger')
        else:
            users.insert_one({
                'username': username,
                'email': email,
                'password': hashed_password,
                'is_master': username == 'admin'  # Set first admin as master
            })
            flash('Account created successfully! Please login', 'success')
            return redirect(url_for('login'))
    
    return render_template('new_signup.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/view_block/<int:block_index>/<filename>')
@login_required
def view_block(block_index, filename):
    """Display block contents for authenticated user"""
    chain_addr = f"{ADDR}/chain"
    resp = requests.get(chain_addr)
    if resp.status_code == 200:
        chain = json.loads(resp.content.decode())
        for block in chain["chain"]:
            if block["index"] == block_index:
                for trans in block["transactions"]:
                    if (trans.get("v_file") == filename and 
                        (current_user.username == trans.get("owner") or 
                         current_user.is_master)):
                        return render_template("new_view_block.html",
                            block=block,
                            transaction=trans,
                            filename=filename)
        flash('Block or file not found', 'danger')
    return redirect(url_for('index'))

# Create a list of requests that peers has send to upload files
def get_tx_req():
    global request_tx
    chain_addr = "{0}/chain".format(ADDR)
    resp = requests.get(chain_addr)
    if resp.status_code == 200:
        content = []
        chain = json.loads(resp.content.decode())
        for block in chain["chain"]:
            for trans in block["transactions"]:
                trans["index"] = block["index"]
                trans["hash"] = block["prev_hash"]
                content.append(trans)
        request_tx = sorted(content,key=lambda k: k["hash"],reverse=True)


# Loads and runs the home page
@app.route("/generate_dataset/<filename>", methods=["POST"])
@login_required
def generate_dataset(filename):
    """Generate a synthetic dataset from the uploaded file."""
    # Decrypt the file
    enc_file_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{os.path.splitext(filename)[0]}.enc")  # Correct reference
    decrypted_file_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{os.path.splitext(filename)[0]}.csv")
    password = current_user.password if current_user.is_authenticated else "anonymous"
    
    # Decrypt the file
    file_encryptor.decrypt_file(enc_file_path, decrypted_file_path, password)
    
    # Call gan.py to generate the dataset and capture the returned synthetic_data and analysis_results
    from app import gan
    synthetic_data, analysis_results = gan.generate_synthetic_data(decrypted_file_path, current_user.username, filename, output_dir=app.config['UPLOAD_FOLDER'])  # Pass username and filename
    
    # Convert synthetic_data DataFrame to HTML for display
    synthetic_data_html = synthetic_data.to_html(classes="table table-striped", index=False)
    
    # Debug log synthetic_csv_path
    synthetic_csv_path = analysis_results.get('synthetic_csv_path')
    app.logger.debug(f"synthetic_csv_path: {synthetic_csv_path}")
    
    flash('Synthetic dataset generated successfully!', 'success')
    # Render a new template to display analysis_results and synthetic_data_html
    return render_template("analysis_results.html", analysis=analysis_results, filename=filename, synthetic_data_html=synthetic_data_html, synthetic_csv_path=synthetic_csv_path)
    
@app.route("/download_synthetic/<path:filepath>")
@login_required
def download_synthetic(filepath):
    """Serve the synthetic dataset CSV file for download, including subdirectories."""
    from flask import send_from_directory, abort
    directory = app.config['UPLOAD_FOLDER']
    try:
        # filepath can include subdirectories, so use safe_join to prevent path traversal
        from werkzeug.utils import safe_join
        safe_path = safe_join(directory, filepath)
        app.logger.debug(f"Requested filepath: {filepath}")
        app.logger.debug(f"Safe joined path: {safe_path}")
        app.logger.debug(f"File exists: {os.path.isfile(safe_path) if safe_path else 'safe_path is None'}")
        if not safe_path or not os.path.isfile(safe_path):
            app.logger.error(f"File not found for download: {filepath} in {directory}")
            abort(404)
        # send_from_directory requires filename relative to directory
        return send_from_directory(directory, filepath, as_attachment=True)
    except Exception as e:
        app.logger.error(f"Error serving file {filepath}: {str(e)}")
        abort(500)

@app.route("/")
def index():
    get_tx_req()
    # Show anonymous files to all, private files only to owner/master
    filtered_tx = [tx for tx in request_tx 
                  if 'v_file' in tx and 
                  (tx.get('owner') == 'anonymous' or
                   (current_user.is_authenticated and 
                    (current_user.username == tx.get('owner') or 
                     current_user.is_master)))]
    return render_template("new_index.html",
                         title="FileStorage",
                         subtitle="A Decentralized Network for File Storage/Sharing",
                         node_address=ADDR,
                         request_tx=filtered_tx)


@app.route("/submit", methods=["POST"])
# When new transaction is created it is processed and added to transaction
def submit():
    start = timer()
    user = request.form["user"]
    up_file = request.files["v_file"]
    
    # Get base filename without extension
    base_filename = os.path.splitext(secure_filename(up_file.filename))[0]
    
    # Save directly to encrypted file
    enc_file_path = os.path.join("app/static/Uploads/", f"{base_filename}.enc")
    up_file.save(enc_file_path)  # Save original content temporarily
    
    # Get file size before encryption
    file_states = os.stat(enc_file_path).st_size
    
    # Encrypt the file in-place
    password = current_user.password if current_user.is_authenticated else "anonymous"
    file_encryptor.encrypt_file(enc_file_path, enc_file_path, password)
    
    # Store path to encrypted file
    files[up_file.filename] = enc_file_path
    
    # Read and encode encrypted file data as base64
    with open(files[up_file.filename], 'rb') as f:
        import base64
        file_data = base64.b64encode(f.read()).decode('utf-8')
    
    # Create transaction object
    post_object = {
        "user": user,
        "v_file": up_file.filename,
        "file_data": file_data,  # Now base64 encoded string
        "file_size": file_states,
        "owner": current_user.username if current_user.is_authenticated else "anonymous"
    }
   
    # Submit a new transaction
    address = "{0}/new_transaction".format(ADDR)
    requests.post(address, json=post_object)
    end = timer()
    print(end - start)
    return redirect("/")

#creates a download link for the file
@app.route("/submit/<string:variable>",methods = ["GET"])
@login_required
def download_file(variable):
    # Get file info from blockchain
    chain_addr = "{0}/chain".format(ADDR)
    resp = requests.get(chain_addr)
    if resp.status_code == 200:
        chain = json.loads(resp.content.decode())
        for block in chain["chain"]:
            for trans in block["transactions"]:
                if trans.get("v_file") == variable:
                    # Check if current user is owner, master, or file is anonymous
                    if (current_user.is_authenticated and 
                        (current_user.username == trans.get("owner") or 
                         current_user.is_master)) or trans.get("owner") == "anonymous":
                        # Create temp file for decryption using base filename with .csv
                        base_filename = os.path.splitext(variable)[0]
                        temp_path = os.path.join(app.root_path, "static", "Uploads", f"temp_{base_filename}.csv")
                        password = current_user.password if current_user.is_authenticated else "anonymous"
                        file_encryptor.decrypt_file(files[variable], temp_path, password)
                        
                        try:
                            # First try normal deletion after send
                            response = send_file(temp_path, as_attachment=True)
                            
                            def delete_temp_file(filepath):
                                """Force delete file with multiple strategies"""
                                try:
                                    # First try normal deletion
                                    if os.path.exists(filepath):
                                        os.remove(filepath)
                                        app.logger.debug(f"Deleted temp file: {filepath}")
                                        return
                                    
                                    # If file doesn't exist, nothing to do
                                    app.logger.warning(f"Temp file not found: {filepath}")
                                    return
                                    
                                except Exception as e:
                                    app.logger.warning(f"First delete attempt failed: {str(e)}")
                                    
                                    # If normal delete fails, try forcing it after a delay
                                    import time
                                    time.sleep(1)  # Wait for file handles to release
                                    try:
                                        if os.path.exists(filepath):
                                            os.remove(filepath)
                                            app.logger.debug(f"Force deleted after delay: {filepath}")
                                            return
                                    except Exception as e:
                                        app.logger.warning(f"Force delete failed: {str(e)}")
                                        
                                        # Final fallback - rename and delete
                                        try:
                                            if os.path.exists(filepath):
                                                temp_name = f"{filepath}.deleteme"
                                                os.rename(filepath, temp_name)
                                                os.remove(temp_name)
                                                app.logger.debug(f"Used rename-and-delete for: {filepath}")
                                        except Exception as e:
                                            app.logger.error(f"All deletion attempts failed for {filepath}: {str(e)}")
                            
                            # Ensure cleanup runs even if response fails
                            try:
                                response = send_file(temp_path, as_attachment=True)
                                response.call_on_close(lambda: delete_temp_file(temp_path))
                                return response
                            except:
                                delete_temp_file(temp_path)
                                raise
                            
                        except Exception as e:
                            # Immediate cleanup if download fails
                            try:
                                if os.path.exists(temp_path):
                                    os.remove(temp_path)
                            except:
                                try:
                                    temp_name = f"{temp_path}.deleteme"
                                    os.rename(temp_path, temp_name)
                                    os.remove(temp_name)
                                except:
                                    pass
                            flash('File download failed', 'danger')
                            return redirect(url_for('index'))
                    else:
                        flash('You do not have permission to access this file', 'danger')
                        return redirect(url_for('index'))
    flash('File not found', 'danger')
    return redirect(url_for('index'))