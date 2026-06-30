from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
import os
import json
import ollama
from werkzeug.utils import secure_filename

app = Flask(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'genealogy.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DB_PATH}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'your-secret-key-here'
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

app_config = {
    'model': 'llama3.2:3b',
    'temperature': 0.7
}

db = SQLAlchemy(app)

# ---------- МОДЕЛИ ----------
class Person(db.Model):
    __tablename__ = 'person'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    surname = db.Column(db.String(100), nullable=False)
    maiden_name = db.Column(db.String(100))
    gender = db.Column(db.String(1), default='U')          # M, F, U
    birth_year = db.Column(db.Integer)
    birth_date = db.Column(db.String(20))                  # YYYY-MM-DD
    death_year = db.Column(db.Integer)
    death_date = db.Column(db.String(20))                  # YYYY-MM-DD
    birth_place = db.Column(db.String(200))
    death_place = db.Column(db.String(200))
    profession = db.Column(db.String(200))
    parent_id = db.Column(db.Integer, db.ForeignKey('person.id'), nullable=True)
    father_id = db.Column(db.Integer, db.ForeignKey('person.id'), nullable=True)
    mother_id = db.Column(db.Integer, db.ForeignKey('person.id'), nullable=True)
    spouse_id = db.Column(db.Integer, db.ForeignKey('person.id'), nullable=True)

    generation = db.Column(db.Integer, default=0)
    bio = db.Column(db.Text)
    photo_url = db.Column(db.String(255))

    children = db.relationship(
        'Person',
        backref=db.backref('parent_node', remote_side=[id]),
        lazy='dynamic',
        foreign_keys=[parent_id]
    )
    father = db.relationship('Person', foreign_keys=[father_id], remote_side=[id], uselist=False)
    mother = db.relationship('Person', foreign_keys=[mother_id], remote_side=[id], uselist=False)
    spouse_rel = db.relationship('Person', foreign_keys=[spouse_id], remote_side=[id], uselist=False)

    def get_patronymic(self):
        if not self.parent_node or self.parent_node.gender != 'M':
            return ""
        father_name = self.parent_node.name
        if father_name.endswith('й'):
            base = father_name[:-1]
            suffix = 'евич' if self.gender == 'M' else 'евна'
            return base + suffix
        elif father_name.endswith('я'):
            base = father_name[:-1]
            suffix = 'ич' if self.gender == 'M' else 'инична'
            return base + suffix
        elif father_name in ['Лев', 'Пётр', 'Василий']:
            exceptions = {
                'Лев': 'Львович' if self.gender == 'M' else 'Львовна',
                'Пётр': 'Петрович' if self.gender == 'M' else 'Петровна',
                'Василий': 'Васильевич' if self.gender == 'M' else 'Васильевна'
            }
            return exceptions.get(father_name, father_name + ('ович' if self.gender == 'M' else 'овна'))
        else:
            suffix = 'ович' if self.gender == 'M' else 'овна'
            return father_name + suffix

    @property
    def full_name(self):
        patronymic = self.get_patronymic()
        if patronymic:
            return f"{self.name} {patronymic} {self.surname}"
        return f"{self.name} {self.surname}"

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'surname': self.surname,
            'maiden_name': self.maiden_name,
            'gender': self.gender,
            'patronymic': self.get_patronymic(),
            'full_name': self.full_name,
            'birth_year': self.birth_year,
            'birth_date': self.birth_date,
            'death_year': self.death_year,
            'death_date': self.death_date,
            'birth_place': self.birth_place,
            'death_place': self.death_place,
            'generation': self.generation,
            'parent_id': self.parent_id,
            'father_id': self.father_id,
            'mother_id': self.mother_id,
            'spouse_id': self.spouse_id,
            'bio': self.bio,
            'photo_url': self.photo_url,
            'father_name': self.father.full_name if self.father else None,
            'mother_name': self.mother.full_name if self.mother else None,
            'spouse_name': self.spouse_rel.full_name if self.spouse_rel else None
        }

class LifeEvent(db.Model):
    __tablename__ = 'life_event'
    id = db.Column(db.Integer, primary_key=True)
    person_id = db.Column(db.Integer, db.ForeignKey('person.id'), nullable=False)
    event_type = db.Column(db.String(50), nullable=False)
    year_start = db.Column(db.Integer)
    year_end = db.Column(db.Integer)
    place = db.Column(db.String(200))
    description = db.Column(db.Text)

    person = db.relationship('Person', backref=db.backref('life_events', lazy=True))

class Personality(db.Model):
    __tablename__ = 'personality'
    id = db.Column(db.Integer, primary_key=True)
    person_id = db.Column(db.Integer, db.ForeignKey('person.id'), nullable=False, unique=True)
    friendliness = db.Column(db.Integer, default=5)
    emotionality = db.Column(db.Integer, default=5)
    strictness = db.Column(db.Integer, default=5)
    talkativeness = db.Column(db.Integer, default=5)
    traditionalism = db.Column(db.Integer, default=5)
    humor = db.Column(db.Integer, default=5)
    curiosity = db.Column(db.Integer, default=5)

    person = db.relationship('Person', backref=db.backref('personality', uselist=False))

class FamilyPhoto(db.Model):
    __tablename__ = 'family_photo'
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    url = db.Column(db.String(255), nullable=False)
    description = db.Column(db.String(255), default='')
    file_type = db.Column(db.String(10), default='image')
    uploaded_at = db.Column(db.DateTime, server_default=db.func.now())

    def to_dict(self):
        return {
            'id': self.id,
            'url': self.url,
            'filename': self.filename,
            'description': self.description,
            'file_type': self.file_type
        }

def recalculate_generations():
    persons = Person.query.all()
    if not persons:
        return
    roots = [p for p in persons if not p.parent_id]
    if not roots:
        root = min(persons, key=lambda p: p.id)
    else:
        root = min(roots, key=lambda p: p.id)
    for p in persons:
        p.generation = 0
    def set_gen(p, gen):
        p.generation = gen
        for child in p.children:
            set_gen(child, gen + 1)
    set_gen(root, 0)
    db.session.commit()

def auto_evaluate_personality(person_id):
    person = db.session.get(Person, person_id)
    if not person:
        return None
    traits = {
        'friendliness': 5, 'emotionality': 5, 'strictness': 5,
        'talkativeness': 5, 'traditionalism': 5, 'humor': 5, 'curiosity': 5
    }
    occupations = [e.description for e in LifeEvent.query.filter_by(person_id=person_id, event_type='occupation').all()]
    if occupations:
        if any('воен' in occ.lower() for occ in occupations):
            traits['strictness'] += 2; traits['talkativeness'] -= 1
        if any('учитель' in occ.lower() or 'педагог' in occ.lower() for occ in occupations):
            traits['friendliness'] += 1; traits['traditionalism'] += 1
        if any('врач' in occ.lower() or 'доктор' in occ.lower() for occ in occupations):
            traits['friendliness'] += 2; traits['emotionality'] += 1
    places = [e.place for e in LifeEvent.query.filter_by(person_id=person_id, event_type='residence').all() if e.place]
    if places:
        if any('деревня' in p.lower() or 'село' in p.lower() for p in places):
            traits['traditionalism'] += 2; traits['curiosity'] -= 1
        if any('город' in p.lower() or 'москва' in p.lower() or 'петербург' in p.lower() for p in places):
            traits['curiosity'] += 1
    if person.birth_year:
        if person.birth_year < 1917:
            traits['traditionalism'] += 2; traits['strictness'] += 1
        elif 1917 <= person.birth_year <= 1945:
            traits['strictness'] += 1; traits['emotionality'] += 1
        elif 1946 <= person.birth_year <= 1991:
            traits['curiosity'] += 1
    children_count = Person.query.filter(Person.parent_id == person_id).count()
    if children_count >= 3:
        traits['friendliness'] += 1
    if person.spouse_id:
        traits['emotionality'] += 1
    for k in traits:
        traits[k] = max(1, min(10, traits[k]))
    return traits

ollama_client = ollama.Client(host='http://127.0.0.1:11434')
HISTORICAL_EVENTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'historical_events.json')
try:
    with open(HISTORICAL_EVENTS_PATH, 'r', encoding='utf-8') as f:
        HISTORICAL_EVENTS = json.load(f)
except Exception:
    HISTORICAL_EVENTS = []

# ---------- АУТЕНТИФИКАЦИЯ (упрощённая) ----------
USERS = {'admin': 'admin'}  # временно

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username in USERS and USERS[username] == password:
            session['user_id'] = username
            return redirect(url_for('dashboard'))
        else:
            return render_template('login.html', error='Неверный логин или пароль')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username in USERS:
            return render_template('register.html', error='Пользователь уже существует')
        USERS[username] = password
        session['user_id'] = username
        return redirect(url_for('dashboard'))
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('landing'))

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# ---------- СТРАНИЦЫ ----------
@app.route('/')
def landing():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('landing.html')

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html', username=session['user_id'])

@app.route('/tree')
@login_required
def tree():
    return render_template('index.html')

@app.route('/map')
@login_required
def map_page():
    return render_template('map.html')

@app.route('/gallery')
@login_required
def gallery_page():
    return render_template('gallery.html')

@app.route('/ai-avatars')
@login_required
def ai_avatars_page():
    return render_template('ai_avatars.html')

@app.route('/settings')
@login_required
def settings_page():
    return render_template('settings.html')

@app.route('/profile')
@login_required
def profile_list():
    persons = Person.query.order_by(Person.generation.asc(), Person.name.asc()).all()
    return render_template('profile_list.html', persons=persons)

@app.route('/profile/<int:person_id>')
@login_required
def profile_page(person_id):
    person = db.session.get(Person, person_id)
    if not person:
        return "Человек не найден", 404
    return render_template('person_profile.html', person_id=person_id)

@app.route('/groups')
@login_required
def groups_list():
    return render_template('groups.html', groups=[])

@app.route('/groups/create')
@login_required
def create_group():
    return render_template('create_group.html')

@app.route('/groups/<int:group_id>/settings')
@login_required
def group_settings(group_id):
    return render_template('group_settings.html', group_id=group_id)

# ---------- API ----------
@app.route('/api/persons', methods=['GET'])
def get_persons():
    persons = Person.query.order_by(Person.generation.asc(), Person.name.asc()).all()
    return jsonify([p.to_dict() for p in persons])

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ИСПРАВЛЕНИЕ 1: Добавлен роут для правого клика мыши на Древе
@app.route('/api/person/<int:person_id>/set-root', methods=['POST'])
def set_root_person(person_id):
    session['root_person_id'] = person_id
    return jsonify({'success': True, 'root_id': person_id})

@app.route('/api/person', methods=['POST'])
def add_person():
    try:
        if request.content_type and 'multipart/form-data' in request.content_type:
            data = request.form
            name = data.get('name')
            surname = data.get('surname')
            gender = data.get('gender')
            birth_date = data.get('birth_date')
            death_date = data.get('death_date')
            birth_place = data.get('birth_place')
            death_place = data.get('death_place')
            father_id = data.get('father_id')
            mother_id = data.get('mother_id')
            bio = data.get('bio')
            photo = request.files.get('photo')
        else:
            data = request.get_json()
            name = data.get('name')
            surname = data.get('surname')
            gender = data.get('gender')
            birth_date = data.get('birth_date')
            death_date = data.get('death_date')
            birth_place = data.get('birth_place')
            death_place = data.get('death_place')
            father_id = data.get('father_id')
            mother_id = data.get('mother_id')
            bio = data.get('bio')
            photo = None

        if not name or not surname:
            return jsonify({'error': 'Имя и фамилия обязательны'}), 400

        parent_id = father_id if father_id else None

        new_person = Person(
            name=name,
            surname=surname,
            gender=gender or 'U',
            birth_date=birth_date,
            death_date=death_date,
            birth_place=birth_place,
            death_place=death_place,
            father_id=father_id or None,
            mother_id=mother_id or None,
            parent_id=parent_id,
            bio=bio
        )
        if birth_date and len(birth_date) >= 4:
            try:
                new_person.birth_year = int(birth_date[:4])
            except:
                pass
        if death_date and len(death_date) >= 4:
            try:
                new_person.death_year = int(death_date[:4])
            except:
                pass

        db.session.add(new_person)
        db.session.commit()

        if photo and photo.filename and allowed_file(photo.filename):
            ext = photo.filename.rsplit('.', 1)[1].lower()
            filename = f"person_{new_person.id}_{secure_filename(photo.filename)}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            photo.save(filepath)
            new_person.photo_url = f"/static/uploads/{filename}"
            db.session.commit()

        recalculate_generations()
        return jsonify(new_person.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/api/person/<int:id>', methods=['PUT'])
def update_person(id):
    try:
        person = db.session.get(Person, id)
        if not person:
            return jsonify({'error': 'Персона не найдена'}), 404
        data = request.get_json()

        person.name = data.get('name', person.name)
        person.surname = data.get('surname', person.surname)
        person.gender = data.get('gender', person.gender)
        person.birth_date = data.get('birth_date', person.birth_date)
        person.death_date = data.get('death_date', person.death_date)
        person.birth_place = data.get('birth_place', person.birth_place)
        person.death_place = data.get('death_place', person.death_place)
        person.father_id = data.get('father_id', person.father_id)
        person.mother_id = data.get('mother_id', person.mother_id)
        if data.get('father_id'):
            person.parent_id = data.get('father_id')
        elif data.get('mother_id'):
            person.parent_id = data.get('mother_id')
        person.bio = data.get('bio', person.bio)

        if person.birth_date and len(person.birth_date) >= 4:
            try:
                person.birth_year = int(person.birth_date[:4])
            except:
                pass
        if person.death_date and len(person.death_date) >= 4:
            try:
                person.death_year = int(person.death_date[:4])
            except:
                pass

        db.session.commit()
        recalculate_generations()
        return jsonify(person.to_dict())
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/api/person/<int:id>', methods=['DELETE'])
def delete_person(id):
    try:
        person = db.session.get(Person, id)
        if not person:
            return jsonify({'error': 'Персона не найдена'}), 404
        for child in Person.query.filter((Person.father_id == id) | (Person.mother_id == id)).all():
            if child.father_id == id:
                child.father_id = None
            if child.mother_id == id:
                child.mother_id = None
        db.session.delete(person)
        db.session.commit()
        recalculate_generations()
        return jsonify({'message': 'Deleted'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/seed')
def seed_data():
    if Person.query.first():
        return jsonify({'message': 'Already seeded'})
    
    ivan = Person(name="Иван", surname="Иванов", gender="M", birth_year=1900, bio="Дедушка", birth_place="Москва")
    maria = Person(name="Мария", surname="Иванова", gender="F", birth_year=1905, bio="Бабушка", maiden_name="Петрова")
    petr = Person(name="Пётр", surname="Иванов", gender="M", birth_year=1930, bio="Папа")
    anna = Person(name="Анна", surname="Петрова", gender="F", birth_year=1935, bio="Мама")
    alexey = Person(name="Алексей", surname="Иванов", gender="M", birth_year=1970, bio="Вы")
    
    db.session.add_all([ivan, maria, petr, anna, alexey])
    db.session.flush()
    
    ivan.spouse_id = maria.id
    maria.spouse_id = ivan.id
    
    petr.parent_id = ivan.id
    petr.father_id = ivan.id
    petr.mother_id = maria.id
    
    # ИСПРАВЛЕНИЕ 2: У Анны убран parent_id = ivan.id, чтобы она не была сестрой Петра
    
    petr.spouse_id = anna.id
    anna.spouse_id = petr.id
    
    alexey.parent_id = petr.id
    alexey.father_id = petr.id
    alexey.mother_id = anna.id
    
    db.session.commit()
    recalculate_generations()
    return jsonify({'message': 'Seeded test data'})

@app.route('/reset', methods=['POST'])
def reset_db():
    db.drop_all()
    db.create_all()
    return jsonify({'message': 'DB Reset'})

# ---------- СОБЫТИЯ ----------
@app.route('/api/person/<int:person_id>/events', methods=['GET'])
def get_person_events(person_id):
    events = LifeEvent.query.filter_by(person_id=person_id).order_by(LifeEvent.year_start.asc()).all()
    return jsonify([{
        'id': e.id, 'event_type': e.event_type,
        'year_start': e.year_start, 'year_end': e.year_end,
        'place': e.place, 'description': e.description
    } for e in events])

@app.route('/api/person/<int:person_id>/events', methods=['POST'])
def add_person_event(person_id):
    try:
        data = request.get_json()
        event = LifeEvent(
            person_id=person_id, event_type=data.get('event_type', 'other'),
            year_start=data.get('year_start'), year_end=data.get('year_end'),
            place=data.get('place'), description=data.get('description', '')
        )
        db.session.add(event)
        db.session.commit()
        return jsonify({'id': event.id}), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/api/event/<int:event_id>', methods=['DELETE'])
def delete_event(event_id):
    event = db.session.get(LifeEvent, event_id)
    if not event:
        return jsonify({'error': 'Событие не найдено'}), 404
    db.session.delete(event)
    db.session.commit()
    return jsonify({'message': 'Deleted'})

# ---------- ХАРАКТЕР ----------
@app.route('/api/person/<int:person_id>/personality', methods=['GET'])
def get_personality(person_id):
    pers = Personality.query.filter_by(person_id=person_id).first()
    if pers:
        return jsonify({
            'friendliness': pers.friendliness, 'emotionality': pers.emotionality,
            'strictness': pers.strictness, 'talkativeness': pers.talkativeness,
            'traditionalism': pers.traditionalism, 'humor': pers.humor,
            'curiosity': pers.curiosity
        })
    return jsonify({'error': 'Not found'}), 404

@app.route('/api/person/<int:person_id>/personality', methods=['POST'])
def save_personality(person_id):
    try:
        data = request.get_json()
        pers = Personality.query.filter_by(person_id=person_id).first()
        if not pers:
            pers = Personality(person_id=person_id)
            db.session.add(pers)
        for field in ['friendliness', 'emotionality', 'strictness', 'talkativeness', 'traditionalism', 'humor', 'curiosity']:
            if field in data:
                setattr(pers, field, max(1, min(10, int(data[field]))))
        db.session.commit()
        return jsonify({'message': 'Saved'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/api/person/<int:person_id>/personality/auto', methods=['GET'])
def auto_personality(person_id):
    traits = auto_evaluate_personality(person_id)
    if traits is None:
        return jsonify({'error': 'Person not found'}), 404
    return jsonify(traits)

# ---------- AI ГЕНЕРАЦИЯ БИОГРАФИИ ----------
def collect_parents(person):
    parents = []
    if person.father_id:
        father = db.session.get(Person, person.father_id)
        if father: parents.append(father)
    if person.mother_id:
        mother = db.session.get(Person, person.mother_id)
        if mother: parents.append(mother)
    return parents

def generate_ai_biography(person_id, extra_facts=None):
    person = db.session.get(Person, person_id)
    if not person:
        return None
    facts = []
    facts.append(f"ФИО: {person.full_name}")
    if person.maiden_name: facts.append(f"Девичья фамилия: {person.maiden_name}")
    if person.birth_date: facts.append(f"Дата рождения: {person.birth_date}")
    elif person.birth_year: facts.append(f"Год рождения: {person.birth_year}")
    if person.birth_place: facts.append(f"Место рождения: {person.birth_place}")
    if person.death_date: facts.append(f"Дата смерти: {person.death_date}")
    elif person.death_year: facts.append(f"Год смерти: {person.death_year}")
    if person.death_place: facts.append(f"Место смерти: {person.death_place}")
    events = LifeEvent.query.filter_by(person_id=person_id).order_by(LifeEvent.year_start.asc()).all()
    if events:
        facts.append("Ключевые события жизни (строго опирайся на них):")
        for e in events:
            parts = [f"• {e.event_type}"]
            if e.year_start:
                year_range = str(e.year_start) + (f"–{e.year_end}" if e.year_end else "")
                parts.append(f"({year_range})")
            if e.place: parts.append(f"в {e.place}")
            if e.description: parts.append(f"— {e.description}")
            facts.append(" ".join(parts))
    parents = collect_parents(person)
    if parents:
        parent_info = []
        for p in parents:
            pi = f"{p.full_name} ({p.birth_year or '?'} – {p.death_year or '?'})"
            parent_events = LifeEvent.query.filter_by(person_id=p.id).limit(3).all()
            if parent_events:
                pi += " [события: " + "; ".join(e.description or e.event_type for e in parent_events) + "]"
            parent_info.append(pi)
        facts.append(f"Родители: {'; '.join(parent_info)}")
    spouse = db.session.get(Person, person.spouse_id) if person.spouse_id else None
    if spouse:
        spouse_info = f"{spouse.full_name} ({spouse.birth_year or '?'} – {spouse.death_year or '?'})"
        spouse_events = LifeEvent.query.filter_by(person_id=spouse.id).limit(3).all()
        if spouse_events:
            spouse_info += " [события: " + "; ".join(e.description or e.event_type for e in spouse_events) + "]"
        facts.append(f"Супруг(а): {spouse_info}")
    children = Person.query.filter((Person.father_id == person.id) | (Person.mother_id == person.id)).all()
    if children:
        child_info = []
        for c in children:
            ci = f"{c.full_name} ({c.birth_year or '?'} – {c.death_year or '?'})"
            child_events = LifeEvent.query.filter_by(person_id=c.id).limit(3).all()
            if child_events:
                ci += " [события: " + "; ".join(e.description or e.event_type for e in child_events) + "]"
            child_info.append(ci)
        facts.append(f"Дети: {'; '.join(child_info)}")
    if extra_facts:
        facts.extend(extra_facts)
    prompt = ("Ты — профессиональный русскоязычный генеалог. Напиши краткую литературную биографию человека "
              "на русском языке. Используй ТОЛЬКО факты из предоставленного списка. "
              "НЕ ВЫДУМЫВАЙ ничего, чего нет в списке. "
              "Если каких-то данных нет, просто пропусти этот аспект. "
              "Стиль — спокойный, уважительный, как в семейной хронике.\n\n"
              "Факты:\n" + "\n".join(facts))
    try:
        response = ollama_client.generate(
            model=app_config['model'], prompt=prompt,
            options={'temperature': app_config['temperature']}
        )
        biography = response['response'].strip()
    except Exception:
        biography = "Биография создана на основе следующих данных:\n" + "\n".join(facts)
    return biography

@app.route('/api/generate-bio/<int:person_id>', methods=['POST'])
def api_generate_bio(person_id):
    try:
        data = request.get_json()
        extra_facts = data.get('facts', []) if data else []
        bio = generate_ai_biography(person_id, extra_facts=extra_facts)
        if bio is None:
            return jsonify({'error': 'Персона не найдена'}), 404
        return jsonify({'biography': bio})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ---------- AI-АВАТАР: ЧАТ ----------
def get_persona_prompt(person_id):
    person = db.session.get(Person, person_id)
    if not person: return None
    facts = []
    facts.append(f"Тебя зовут {person.full_name}.")
    if person.maiden_name: facts.append(f"Твоя девичья фамилия: {person.maiden_name}.")
    if person.birth_date: facts.append(f"Ты родился(ась) {person.birth_date}.")
    elif person.birth_year: facts.append(f"Ты родился(ась) в {person.birth_year} году.")
    if person.birth_place: facts.append(f"Ты родился(ась) в {person.birth_place}.")
    if person.death_date: facts.append(f"Ты умер(ла) {person.death_date}.")
    elif person.death_year: facts.append(f"Ты умер(ла) в {person.death_year} году.")
    if person.death_place: facts.append(f"Ты умер(ла) в {person.death_place}.")
    parents = collect_parents(person)
    if parents: facts.append(f"Твои родители: {', '.join(p.full_name for p in parents)}.")
    spouse = db.session.get(Person, person.spouse_id) if person.spouse_id else None
    if spouse: facts.append(f"Твой супруг(а): {spouse.full_name}.")
    children = Person.query.filter((Person.father_id == person.id) | (Person.mother_id == person.id)).all()
    if children: facts.append(f"Твои дети: {', '.join(c.full_name for c in children)}.")
    if person.parent_id:
        siblings = Person.query.filter((Person.parent_id == person.parent_id) & (Person.id != person.id)).all()
        if siblings: facts.append(f"Твои братья/сёстры: {', '.join(s.full_name for s in siblings)}.")
    events = LifeEvent.query.filter_by(person_id=person_id).order_by(LifeEvent.year_start.asc()).all()
    if events:
        event_strs = []
        for e in events:
            parts = [e.event_type]
            if e.year_start: parts.append(f"в {e.year_start}" + (f"–{e.year_end}" if e.year_end else ""))
            if e.place: parts.append(f"в {e.place}")
            if e.description: parts.append(f"({e.description})")
            event_strs.append(" ".join(parts))
        facts.append("Ключевые события твоей жизни: " + "; ".join(event_strs) + ".")
    if person.bio: facts.append(f"Твоя краткая биография: {person.bio}")
    pers = Personality.query.filter_by(person_id=person_id).first()
    if pers:
        traits_desc = (f"Твой характер (оценка 1–10): "
                      f"дружелюбие: {pers.friendliness}, эмоциональность: {pers.emotionality}, "
                      f"строгость: {pers.strictness}, разговорчивость: {pers.talkativeness}, "
                      f"традиционность: {pers.traditionalism}, юмор: {pers.humor}, "
                      f"любознательность: {pers.curiosity}.")
    else:
        auto = auto_evaluate_personality(person_id)
        if auto:
            traits_desc = (f"Твой приблизительный характер (1–10): "
                          f"дружелюбие: {auto['friendliness']}, эмоциональность: {auto['emotionality']}, "
                          f"строгость: {auto['strictness']}, разговорчивость: {auto['talkativeness']}, "
                          f"традиционность: {auto['traditionalism']}, юмор: {auto['humor']}, "
                          f"любознательность: {auto['curiosity']}.")
        else:
            traits_desc = "Твой характер неизвестен, будь нейтральным."
    facts.append(traits_desc)
    persona_prompt = ("Ты — реальный человек, живший в прошлом. Ты отвечаешь на вопросы, используя ТОЛЬКО факты из своей биографии. "
                      "Если тебя спрашивают о том, чего ты не знаешь, говори: 'Я не помню такого' или 'Мне об этом ничего не известно'. "
                      "Отвечай от первого лица. Стиль общения соответствует твоему характеру.\n\n" + "\n".join(facts))
    return persona_prompt

# ИСПРАВЛЕНИЕ 3: Поддержка обоих форматов сообщений (массив/строка) для Чат-бота
@app.route('/api/chat/<int:person_id>', methods=['POST'])
def chat_with_avatar(person_id):
    try:
        data = request.get_json()
        user_message = ""
        
        if 'messages' in data and isinstance(data['messages'], list) and len(data['messages']) > 0:
            user_message = data['messages'][-1].get('content', '').strip()
        else:
            user_message = data.get('message', '').strip()
            
        if not user_message:
            return jsonify({'error': 'Сообщение не может быть пустым'}), 400
            
        persona_prompt = get_persona_prompt(person_id)
        if not persona_prompt:
            return jsonify({'error': 'Персона не найдена'}), 404
            
        full_prompt = persona_prompt + f"\n\nСейчас с тобой говорит пользователь. Ответь на его реплику.\nПользователь: {user_message}\nТвой ответ:"
        MAX_PROMPT_LENGTH = 4000
        if len(full_prompt) > MAX_PROMPT_LENGTH:
            full_prompt = full_prompt[:MAX_PROMPT_LENGTH] + "\n...\nТвой ответ (кратко):"
            
        response = ollama_client.generate(
            model=app_config['model'], prompt=full_prompt,
            options={'temperature': app_config['temperature']}
        )
        reply = response['response'].strip()
        return jsonify({'reply': reply})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

# ---------- ЗАГРУЗКА ФОТО ----------
@app.route('/upload-photo/<int:person_id>', methods=['POST'])
def upload_photo(person_id):
    if 'photo' not in request.files:
        return jsonify({'error': 'Файл не выбран'}), 400
    file = request.files['photo']
    if file.filename == '':
        return jsonify({'error': 'Файл не выбран'}), 400
    if file and allowed_file(file.filename):
        ext = file.filename.rsplit('.', 1)[1].lower()
        filename = f"person_{person_id}_{secure_filename(file.filename)}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        person = db.session.get(Person, person_id)
        if person:
            person.photo_url = f"/static/uploads/{filename}"
            db.session.commit()
            return jsonify({'url': person.photo_url})
    return jsonify({'error': 'Ошибка загрузки или недопустимый формат'}), 500

@app.route('/api/person/<int:person_id>/photo', methods=['DELETE'])
def delete_person_photo(person_id):
    person = db.session.get(Person, person_id)
    if not person:
        return jsonify({'error': 'Персона не найдена'}), 404
    if person.photo_url:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], os.path.basename(person.photo_url))
        if os.path.exists(filepath):
            os.remove(filepath)
        person.photo_url = None
        db.session.commit()
        return jsonify({'message': 'Фото удалено'})
    return jsonify({'error': 'У персоны нет фото'}), 400

# ---------- СЕМЕЙНЫЕ ФАЙЛЫ ----------
@app.route('/upload-family-photo', methods=['POST'])
def upload_family_photo():
    if 'photo' not in request.files:
        return jsonify({'error': 'Файл не выбран'}), 400
    file = request.files['photo']
    if file.filename == '':
        return jsonify({'error': 'Файл не выбран'}), 400
    if file:
        ext = os.path.splitext(file.filename)[1].lower()
        if ext in ('.mp4', '.webm', '.mov', '.avi'):
            ftype = 'video'
        elif ext == '.gif':
            ftype = 'gif'
        else:
            ftype = 'image'
        filename = f"family_{secure_filename(file.filename)}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        url = f"/static/uploads/{filename}"
        photo = FamilyPhoto(filename=filename, url=url, file_type=ftype)
        db.session.add(photo)
        db.session.commit()
        return jsonify(photo.to_dict()), 201
    return jsonify({'error': 'Ошибка загрузки'}), 500

@app.route('/api/family-photos')
def get_family_photos():
    photos = FamilyPhoto.query.order_by(FamilyPhoto.uploaded_at.desc()).all()
    return jsonify([p.to_dict() for p in photos])

@app.route('/api/family-photos/<int:photo_id>', methods=['PUT'])
def update_family_photo(photo_id):
    photo = db.session.get(FamilyPhoto, photo_id)
    if not photo:
        return jsonify({'error': 'Файл не найден'}), 404
    data = request.get_json()
    if 'description' in data:
        photo.description = data['description']
        db.session.commit()
        return jsonify(photo.to_dict())
    return jsonify({'error': 'Нет данных'}), 400

@app.route('/api/family-photos/<int:photo_id>', methods=['DELETE'])
def delete_family_photo(photo_id):
    photo = db.session.get(FamilyPhoto, photo_id)
    if not photo:
        return jsonify({'error': 'Файл не найден'}), 404
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], photo.filename)
    if os.path.exists(filepath):
        os.remove(filepath)
    db.session.delete(photo)
    db.session.commit()
    return jsonify({'message': 'Deleted'})

# ---------- НАСТРОЙКИ ----------
@app.route('/api/models')
def get_models():
    try:
        models = ollama_client.list()
        if 'models' in models:
            return jsonify([m['name'] for m in models['models']])
        else:
            return jsonify([])
    except Exception as e:
        print(f"Error listing Ollama models: {e}")
        return jsonify([])

@app.route('/api/config', methods=['GET'])
def get_config():
    return jsonify(app_config)

@app.route('/api/config', methods=['POST'])
def update_config():
    data = request.get_json()
    if 'model' in data:
        app_config['model'] = data['model']
    if 'temperature' in data:
        t = float(data['temperature'])
        app_config['temperature'] = max(0.1, min(1.0, t))
    return jsonify(app_config)

@app.route('/api/export')
def export_data():
    persons = Person.query.all()
    export = []
    for p in persons:
        events = LifeEvent.query.filter_by(person_id=p.id).all()
        pers = Personality.query.filter_by(person_id=p.id).first()
        export.append({
            'person': p.to_dict(),
            'events': [{'type':e.event_type,'year_start':e.year_start,'year_end':e.year_end,'place':e.place,'desc':e.description} for e in events],
            'personality': {'friendliness':pers.friendliness,'emotionality':pers.emotionality,'strictness':pers.strictness,'talkativeness':pers.talkativeness,'traditionalism':pers.traditionalism,'humor':pers.humor,'curiosity':pers.curiosity} if pers else None
        })
    return jsonify(export)

@app.route('/api/system-info')
def system_info():
    import platform, sqlalchemy
    try:
        models = ollama_client.list()
        model_names = [m['name'] for m in models['models']] if 'models' in models else []
    except Exception:
        model_names = []
    return jsonify({
        'python_version': platform.python_version(),
        'flask_version': '3.1.1',
        'sqlalchemy_version': sqlalchemy.__version__,
        'ollama_models': model_names,
        'database_size': os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0,
        'upload_folder': app.config['UPLOAD_FOLDER'],
        'project_version': '1.0 (дипломный проект)'
    })

@app.route('/api/photos')
def get_photos():
    photos = []
    if os.path.exists(app.config['UPLOAD_FOLDER']):
        for f in os.listdir(app.config['UPLOAD_FOLDER']):
            if f.startswith('person_') and os.path.isfile(os.path.join(app.config['UPLOAD_FOLDER'], f)):
                person_id = int(f.split('_')[1])
                person = db.session.get(Person, person_id)
                photos.append({'filename': f, 'url': f'/static/uploads/{f}', 'person': person.full_name if person else 'Неизвестный'})
    return jsonify(photos)

@app.route('/api/photos/<filename>', methods=['DELETE'])
def delete_photo(filename):
    path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if os.path.exists(path):
        os.remove(path)
        return jsonify({'message': 'Deleted'})
    return jsonify({'error': 'Not found'}), 404

def add_new_columns_if_not_exist():
    from sqlalchemy import inspect, text
    engine = db.engine
    inspector = inspect(engine)
    existing_columns = [col['name'] for col in inspector.get_columns('person')]
    with engine.connect() as conn:
        columns_to_add = {
            'birth_place': 'VARCHAR(200)',
            'death_place': 'VARCHAR(200)',
            'gender': 'VARCHAR(1) DEFAULT "U"',
            'birth_date': 'VARCHAR(20)',
            'death_date': 'VARCHAR(20)',
            'profession': 'VARCHAR(200)',
            'father_id': 'INTEGER',
            'mother_id': 'INTEGER'
        }
        for col, col_type in columns_to_add.items():
            if col not in existing_columns:
                try:
                    conn.execute(text(f'ALTER TABLE person ADD COLUMN {col} {col_type}'))
                except Exception as e:
                    print(f"Could not add {col}: {e}")
        conn.commit()

# ИСПРАВЛЕНИЕ 4: Использование db.session.get() вместо устаревшего Person.query.get()
@app.route('/api/dashboard-data')
def dashboard_data():
    from datetime import datetime, timedelta

    total_persons = Person.query.count()
    total_events = LifeEvent.query.count()
    total_photos = FamilyPhoto.query.count()

    recent_persons = Person.query.order_by(Person.id.desc()).limit(5).all()
    recent_persons_data = [{'id': p.id, 'full_name': p.full_name, 'action': 'добавил(а) человека'} for p in recent_persons]

    recent_events = LifeEvent.query.order_by(LifeEvent.id.desc()).limit(5).all()
    recent_events_data = []
    for e in recent_events:
        person = db.session.get(Person, e.person_id) # Исправлено здесь
        person_name = person.full_name if person else 'неизвестный'
        recent_events_data.append({'person_id': e.person_id, 'person_name': person_name, 'event_type': e.event_type, 'year': e.year_start, 'action': f'добавил(а) событие "{e.event_type}"'})

    recent_photos = FamilyPhoto.query.order_by(FamilyPhoto.uploaded_at.desc()).limit(5).all()
    recent_photos_data = [{'id': p.id, 'url': p.url, 'description': p.description or 'Семейное фото'} for p in recent_photos]

    today = datetime.now().date()
    upcoming = []
    all_persons = Person.query.all()
    for p in all_persons:
        if p.birth_date and len(p.birth_date) >= 10:
            try:
                parts = p.birth_date.split('-')
                if len(parts) == 3:
                    month = int(parts[1])
                    day = int(parts[2])
                    birth_this_year = datetime(today.year, month, day).date()
                    if birth_this_year < today:
                        birth_this_year = datetime(today.year + 1, month, day).date()
                    days_until = (birth_this_year - today).days
                    if 0 <= days_until <= 7:
                        upcoming.append({
                            'id': p.id,
                            'full_name': p.full_name,
                            'days': days_until,
                            'birth_date': f"{month:02d}-{day:02d}"
                        })
            except:
                pass
    upcoming.sort(key=lambda x: x['days'])

    return jsonify({
        'stats': {'persons': total_persons, 'events': total_events, 'photos': total_photos},
        'recent_persons': recent_persons_data,
        'recent_events': recent_events_data,
        'recent_photos': recent_photos_data,
        'upcoming_birthdays': upcoming
    })

if __name__ == '__main__':
    with app.app_context():
        add_new_columns_if_not_exist()
        db.create_all()
        if not Person.query.first():
            seed_data()
    app.run(debug=True, host='0.0.0.0', port=5000)
