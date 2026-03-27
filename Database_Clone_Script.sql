-- =====================================================
-- FULL DATABASE CLONE (TABLES + PK + FK + INDEXES)
-- RLS / FUNCTIONS IGNORED AS REQUESTED
-- =====================================================

-- =========================
-- SEQUENCES
-- =========================
CREATE SEQUENCE users_id_seq;
CREATE SEQUENCE subjects_id_seq;
CREATE SEQUENCE questions_id_seq;
CREATE SEQUENCE results_id_seq;
CREATE SEQUENCE ai_chat_history_id_seq;
CREATE SEQUENCE login_attempts_id_seq;
CREATE SEQUENCE ai_usage_tracking_id_seq;
CREATE SEQUENCE categories_id_seq;
CREATE SEQUENCE chat_connections_id_seq;
CREATE SEQUENCE chat_conversations_id_seq;
CREATE SEQUENCE chat_members_id_seq;
CREATE SEQUENCE chat_messages_id_seq;
CREATE SEQUENCE chat_unread_id_seq;
CREATE SEQUENCE exam_attempts_id_seq;
CREATE SEQUENCE exams_id_seq;
CREATE SEQUENCE pw_tokens_id_seq;
CREATE SEQUENCE question_discussions_id_seq;
CREATE SEQUENCE requests_raised_request_id_seq;
CREATE SEQUENCE responses_id_seq;
CREATE SEQUENCE sessions_id_seq;

-- =========================
-- CORE TABLES
-- =========================
CREATE TABLE users (
  id integer PRIMARY KEY DEFAULT nextval('users_id_seq'),
  username varchar NOT NULL UNIQUE,
  email varchar NOT NULL UNIQUE,
  password varchar NOT NULL,
  full_name varchar,
  created_at timestamp DEFAULT now(),
  role varchar DEFAULT 'user',
  updated_at timestamp,
  last_login timestamp,
  username_lower varchar,
  email_lower varchar
);

CREATE TABLE categories (
  id integer PRIMARY KEY DEFAULT nextval('categories_id_seq'),
  name varchar NOT NULL UNIQUE,
  drive_file_id varchar,
  image_url varchar,
  created_at timestamp DEFAULT now()
);

CREATE TABLE subjects (
  id integer PRIMARY KEY DEFAULT nextval('subjects_id_seq'),
  subject_name varchar NOT NULL,
  subject_folder_id varchar,
  subject_folder_created_at timestamp
);

CREATE TABLE login_attempts (
  id integer PRIMARY KEY DEFAULT nextval('login_attempts_id_seq'),
  identifier varchar NOT NULL,
  ip_address varchar NOT NULL,
  failed_count integer DEFAULT 0,
  first_failed_at timestamp DEFAULT now(),
  last_failed_at timestamp DEFAULT now(),
  blocked_until timestamp
);

CREATE TABLE pw_tokens (
  id integer PRIMARY KEY DEFAULT nextval('pw_tokens_id_seq'),
  token varchar NOT NULL UNIQUE,
  email varchar NOT NULL,
  expires_at timestamp NOT NULL,
  used boolean DEFAULT false,
  created_at timestamp DEFAULT now(),
  type varchar
);

-- =========================
-- EXAM SYSTEM
-- =========================
CREATE TABLE exams (
  id integer PRIMARY KEY DEFAULT nextval('exams_id_seq'),
  name varchar NOT NULL,
  date varchar,
  start_time varchar,
  duration integer DEFAULT 60,
  total_questions integer DEFAULT 0,
  status varchar DEFAULT 'draft',
  instructions text,
  positive_marks varchar,
  negative_marks varchar,
  max_attempts integer,
  result_mode varchar DEFAULT 'instant',
  result_delay integer DEFAULT 0,
  results_released boolean DEFAULT false,
  category_id integer REFERENCES categories(id)
);

CREATE TABLE questions (
  id integer PRIMARY KEY DEFAULT nextval('questions_id_seq'),
  exam_id integer REFERENCES exams(id),
  question_text text NOT NULL,
  option_a text,
  option_b text,
  option_c text,
  option_d text,
  correct_answer text,
  question_type varchar DEFAULT 'MCQ',
  image_path text,
  positive_marks integer DEFAULT 1,
  negative_marks numeric DEFAULT 0,
  tolerance numeric DEFAULT 0
);

CREATE TABLE results (
  id integer PRIMARY KEY DEFAULT nextval('results_id_seq'),
  student_id integer REFERENCES users(id),
  exam_id integer,
  score integer DEFAULT 0,
  total_questions integer DEFAULT 0,
  correct_answers integer DEFAULT 0,
  incorrect_answers integer DEFAULT 0,
  unanswered_questions integer DEFAULT 0,
  max_score integer DEFAULT 0,
  percentage numeric DEFAULT 0,
  grade varchar,
  time_taken_minutes integer DEFAULT 0,
  completed_at timestamp DEFAULT now()
);

CREATE TABLE responses (
  id integer PRIMARY KEY DEFAULT nextval('responses_id_seq'),
  result_id integer REFERENCES results(id),
  exam_id integer,
  question_id integer REFERENCES questions(id),
  given_answer text,
  correct_answer text,
  is_correct boolean DEFAULT false,
  marks_obtained numeric DEFAULT 0,
  question_type varchar,
  is_attempted boolean DEFAULT false
);

CREATE TABLE exam_attempts (
  id integer PRIMARY KEY DEFAULT nextval('exam_attempts_id_seq'),
  student_id integer REFERENCES users(id),
  exam_id integer,
  attempt_number integer DEFAULT 1,
  status varchar DEFAULT 'in_progress',
  start_time timestamp,
  end_time timestamp
);

-- =========================
-- DISCUSSION SYSTEM
-- =========================
CREATE TABLE discussion_counts (
  question_id integer PRIMARY KEY REFERENCES questions(id),
  count integer DEFAULT 0
);

CREATE TABLE question_discussions (
  id integer PRIMARY KEY DEFAULT nextval('question_discussions_id_seq'),
  question_id integer REFERENCES questions(id),
  exam_id integer REFERENCES exams(id),
  user_id integer REFERENCES users(id),
  username varchar NOT NULL,
  message text NOT NULL,
  parent_id integer,
  is_pinned boolean DEFAULT false,
  is_best_answer boolean DEFAULT false,
  is_deleted boolean DEFAULT false,
  is_edited boolean DEFAULT false,
  created_at timestamp DEFAULT now(),
  updated_at timestamp DEFAULT now()
);

-- =========================
-- AI SYSTEM
-- =========================
CREATE TABLE ai_chat_history (
  id integer PRIMARY KEY DEFAULT nextval('ai_chat_history_id_seq'),
  user_id integer REFERENCES users(id),
  message text NOT NULL,
  is_user boolean DEFAULT true,
  timestamp timestamp DEFAULT now()
);

CREATE TABLE ai_usage_tracking (
  id integer PRIMARY KEY DEFAULT nextval('ai_usage_tracking_id_seq'),
  user_id integer REFERENCES users(id),
  date date DEFAULT CURRENT_DATE,
  questions_used integer DEFAULT 0
);

-- =========================
-- SESSION MANAGEMENT
-- =========================
CREATE TABLE sessions (
  id integer PRIMARY KEY DEFAULT nextval('sessions_id_seq'),
  token varchar NOT NULL UNIQUE,
  user_id integer REFERENCES users(id),
  device_info text,
  last_seen timestamp DEFAULT now(),
  is_exam_active boolean DEFAULT false,
  exam_id integer,
  result_id integer,
  admin_session boolean DEFAULT false,
  active boolean DEFAULT true,
  created_at timestamp DEFAULT now()
);

-- =========================
-- CHAT SYSTEM
-- =========================
CREATE TABLE chat_conversations (
  id integer PRIMARY KEY DEFAULT nextval('chat_conversations_id_seq'),
  is_group boolean DEFAULT false,
  group_name varchar,
  created_by integer REFERENCES users(id),
  created_at timestamp DEFAULT now()
);

CREATE TABLE chat_connections (
  id integer PRIMARY KEY DEFAULT nextval('chat_connections_id_seq'),
  requester_id integer REFERENCES users(id),
  recipient_id integer REFERENCES users(id),
  status varchar DEFAULT 'pending',
  created_at timestamp DEFAULT now(),
  updated_at timestamp DEFAULT now()
);

CREATE TABLE chat_members (
  id integer PRIMARY KEY DEFAULT nextval('chat_members_id_seq'),
  conversation_id integer REFERENCES chat_conversations(id),
  user_id integer REFERENCES users(id),
  joined_at timestamp DEFAULT now(),
  role varchar DEFAULT 'member'
);

CREATE TABLE chat_messages (
  id integer PRIMARY KEY DEFAULT nextval('chat_messages_id_seq'),
  conversation_id integer REFERENCES chat_conversations(id),
  sender_id integer REFERENCES users(id),
  sender_name varchar NOT NULL,
  message text NOT NULL,
  is_deleted boolean DEFAULT false,
  created_at timestamp DEFAULT now(),
  is_edited boolean DEFAULT false,
  reply_to_id integer,
  reply_to_text varchar,
  reply_to_name varchar
);

CREATE TABLE chat_unread (
  id integer PRIMARY KEY DEFAULT nextval('chat_unread_id_seq'),
  user_id integer REFERENCES users(id),
  conversation_id integer REFERENCES chat_conversations(id),
  count integer DEFAULT 0
);

CREATE TABLE chat_visibility (
  id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id integer REFERENCES users(id),
  conversation_id integer REFERENCES chat_conversations(id),
  cleared_at timestamp DEFAULT now()
);

-- =========================
-- MISC
-- =========================
CREATE TABLE requests_raised (
  request_id integer PRIMARY KEY DEFAULT nextval('requests_raised_request_id_seq'),
  username varchar,
  email varchar,
  current_access varchar,
  requested_access varchar,
  request_date timestamp DEFAULT now(),
  request_status varchar DEFAULT 'pending',
  reason text,
  processed_by varchar,
  processed_date timestamp
);

-- =========================
-- INDEXES (PERFORMANCE)
-- =========================

-- USERS
CREATE INDEX idx_users_username_lower ON users (username_lower);
CREATE INDEX idx_users_email_lower ON users (email_lower);

-- EXAMS
CREATE INDEX idx_exams_category_id ON exams (category_id);

-- QUESTIONS
CREATE INDEX idx_questions_exam_id ON questions (exam_id);

-- RESULTS
CREATE INDEX idx_results_student_id ON results (student_id);
CREATE INDEX idx_results_exam_id ON results (exam_id);

-- RESPONSES
CREATE INDEX idx_responses_result_id ON responses (result_id);
CREATE INDEX idx_responses_question_id ON responses (question_id);

-- DISCUSSIONS
CREATE INDEX idx_qd_question_id ON question_discussions (question_id);
CREATE INDEX idx_qd_user_id ON question_discussions (user_id);

-- AI
CREATE INDEX idx_ai_chat_user_id ON ai_chat_history (user_id);
CREATE INDEX idx_ai_usage_user_date ON ai_usage_tracking (user_id, date);

-- SESSIONS
CREATE INDEX idx_sessions_user_id ON sessions (user_id);

-- CHAT
CREATE INDEX idx_chat_conv_created_by ON chat_conversations (created_by);
CREATE INDEX idx_chat_conn_requester ON chat_connections (requester_id);
CREATE INDEX idx_chat_conn_recipient ON chat_connections (recipient_id);
CREATE INDEX idx_chat_members_conv ON chat_members (conversation_id);
CREATE INDEX idx_chat_members_user ON chat_members (user_id);
CREATE INDEX idx_chat_messages_conv ON chat_messages (conversation_id);
CREATE INDEX idx_chat_messages_sender ON chat_messages (sender_id);
CREATE INDEX idx_chat_unread_user_conv ON chat_unread (user_id, conversation_id);

-- LOGIN
CREATE INDEX idx_login_identifier_ip ON login_attempts (identifier, ip_address);

-- =====================================================
-- END OF FULL DATABASE ARCHITECTURE
-- =====================================================
