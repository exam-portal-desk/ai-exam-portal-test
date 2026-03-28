```sql
-- ============================================================
-- ExamPortal Complete Schema Migration
-- Run this in NEW Supabase project SQL Editor
-- ============================================================

-- ============================================================
-- STEP 1: DROP EXISTING TABLES (fresh start)
-- ============================================================
DROP TABLE IF EXISTS public.chat_visibility CASCADE;
DROP TABLE IF EXISTS public.chat_unread CASCADE;
DROP TABLE IF EXISTS public.chat_messages CASCADE;
DROP TABLE IF EXISTS public.chat_members CASCADE;
DROP TABLE IF EXISTS public.chat_conversations CASCADE;
DROP TABLE IF EXISTS public.chat_connections CASCADE;
DROP TABLE IF EXISTS public.question_discussions CASCADE;
DROP TABLE IF EXISTS public.discussion_counts CASCADE;
DROP TABLE IF EXISTS public.responses CASCADE;
DROP TABLE IF EXISTS public.results CASCADE;
DROP TABLE IF EXISTS public.exam_attempts CASCADE;
DROP TABLE IF EXISTS public.questions CASCADE;
DROP TABLE IF EXISTS public.exams CASCADE;
DROP TABLE IF EXISTS public.ai_chat_history CASCADE;
DROP TABLE IF EXISTS public.ai_usage_tracking CASCADE;
DROP TABLE IF EXISTS public.sessions CASCADE;
DROP TABLE IF EXISTS public.pw_tokens CASCADE;
DROP TABLE IF EXISTS public.login_attempts CASCADE;
DROP TABLE IF EXISTS public.requests_raised CASCADE;
DROP TABLE IF EXISTS public.subjects CASCADE;
DROP TABLE IF EXISTS public.categories CASCADE;
DROP TABLE IF EXISTS public.users CASCADE;

-- ============================================================
-- STEP 2: DROP EXISTING SEQUENCES (if any)
-- ============================================================
DROP SEQUENCE IF EXISTS public.ai_chat_history_id_seq CASCADE;
DROP SEQUENCE IF EXISTS public.ai_usage_tracking_id_seq CASCADE;
DROP SEQUENCE IF EXISTS public.categories_id_seq CASCADE;
DROP SEQUENCE IF EXISTS public.chat_connections_id_seq CASCADE;
DROP SEQUENCE IF EXISTS public.chat_conversations_id_seq CASCADE;
DROP SEQUENCE IF EXISTS public.chat_members_id_seq CASCADE;
DROP SEQUENCE IF EXISTS public.chat_messages_id_seq CASCADE;
DROP SEQUENCE IF EXISTS public.chat_unread_id_seq CASCADE;
DROP SEQUENCE IF EXISTS public.chat_visibility_id_seq CASCADE;
DROP SEQUENCE IF EXISTS public.exam_attempts_id_seq CASCADE;
DROP SEQUENCE IF EXISTS public.exams_id_seq CASCADE;
DROP SEQUENCE IF EXISTS public.login_attempts_id_seq CASCADE;
DROP SEQUENCE IF EXISTS public.pw_tokens_id_seq CASCADE;
DROP SEQUENCE IF EXISTS public.question_discussions_id_seq CASCADE;
DROP SEQUENCE IF EXISTS public.questions_id_seq CASCADE;
DROP SEQUENCE IF EXISTS public.requests_raised_request_id_seq CASCADE;
DROP SEQUENCE IF EXISTS public.responses_id_seq CASCADE;
DROP SEQUENCE IF EXISTS public.results_id_seq CASCADE;
DROP SEQUENCE IF EXISTS public.sessions_id_seq CASCADE;
DROP SEQUENCE IF EXISTS public.subjects_id_seq CASCADE;
DROP SEQUENCE IF EXISTS public.users_id_seq CASCADE;

-- ============================================================
-- STEP 3: CREATE SEQUENCES
-- ============================================================
CREATE SEQUENCE public.ai_chat_history_id_seq;
CREATE SEQUENCE public.ai_usage_tracking_id_seq;
CREATE SEQUENCE public.categories_id_seq;
CREATE SEQUENCE public.chat_connections_id_seq;
CREATE SEQUENCE public.chat_conversations_id_seq;
CREATE SEQUENCE public.chat_members_id_seq;
CREATE SEQUENCE public.chat_messages_id_seq;
CREATE SEQUENCE public.chat_unread_id_seq;
CREATE SEQUENCE public.chat_visibility_id_seq;
CREATE SEQUENCE public.exam_attempts_id_seq;
CREATE SEQUENCE public.exams_id_seq;
CREATE SEQUENCE public.login_attempts_id_seq;
CREATE SEQUENCE public.pw_tokens_id_seq;
CREATE SEQUENCE public.question_discussions_id_seq;
CREATE SEQUENCE public.questions_id_seq;
CREATE SEQUENCE public.requests_raised_request_id_seq;
CREATE SEQUENCE public.responses_id_seq;
CREATE SEQUENCE public.results_id_seq;
CREATE SEQUENCE public.sessions_id_seq;
CREATE SEQUENCE public.subjects_id_seq;
CREATE SEQUENCE public.users_id_seq;

-- ============================================================
-- STEP 4: CREATE TABLES
-- ============================================================

CREATE TABLE public.users (
  id integer DEFAULT nextval('users_id_seq'::regclass) NOT NULL,
  username character varying NOT NULL,
  email character varying NOT NULL,
  password character varying NOT NULL,
  full_name character varying,
  created_at timestamp without time zone DEFAULT now(),
  role character varying DEFAULT 'user'::character varying,
  updated_at timestamp without time zone,
  last_login timestamp without time zone,
  username_lower character varying,
  email_lower character varying,
  CONSTRAINT users_pkey PRIMARY KEY (id),
  CONSTRAINT users_username_key UNIQUE (username),
  CONSTRAINT users_email_key UNIQUE (email)
);

CREATE TABLE public.categories (
  id integer DEFAULT nextval('categories_id_seq'::regclass) NOT NULL,
  name character varying(50) NOT NULL,
  drive_file_id character varying(255) DEFAULT NULL::character varying,
  image_url character varying(500) DEFAULT NULL::character varying,
  created_at timestamp without time zone DEFAULT now(),
  CONSTRAINT categories_pkey PRIMARY KEY (id),
  CONSTRAINT categories_name_key UNIQUE (name)
);

CREATE TABLE public.subjects (
  id integer DEFAULT nextval('subjects_id_seq'::regclass) NOT NULL,
  subject_name character varying NOT NULL,
  subject_folder_id character varying,
  subject_folder_created_at timestamp without time zone,
  CONSTRAINT subjects_pkey PRIMARY KEY (id)
);

CREATE TABLE public.exams (
  id integer DEFAULT nextval('exams_id_seq'::regclass) NOT NULL,
  name character varying NOT NULL,
  date character varying,
  start_time character varying,
  duration integer DEFAULT 60,
  total_questions integer DEFAULT 0,
  status character varying DEFAULT 'draft'::character varying,
  instructions text,
  positive_marks character varying,
  negative_marks character varying,
  max_attempts integer,
  result_mode character varying DEFAULT 'instant'::character varying,
  result_delay integer DEFAULT 0,
  results_released boolean DEFAULT false,
  category_id integer,
  CONSTRAINT exams_pkey PRIMARY KEY (id)
);

CREATE TABLE public.questions (
  id integer DEFAULT nextval('questions_id_seq'::regclass) NOT NULL,
  exam_id integer,
  question_text text NOT NULL,
  option_a text,
  option_b text,
  option_c text,
  option_d text,
  correct_answer text,
  question_type character varying DEFAULT 'MCQ'::character varying,
  image_path text,
  positive_marks integer DEFAULT 1,
  negative_marks numeric DEFAULT 0,
  tolerance numeric DEFAULT 0,
  CONSTRAINT questions_pkey PRIMARY KEY (id)
);

CREATE TABLE public.sessions (
  id integer DEFAULT nextval('sessions_id_seq'::regclass) NOT NULL,
  token character varying NOT NULL,
  user_id integer,
  device_info text,
  last_seen timestamp without time zone DEFAULT now(),
  is_exam_active boolean DEFAULT false,
  exam_id integer,
  result_id integer,
  admin_session boolean DEFAULT false,
  active boolean DEFAULT true,
  created_at timestamp without time zone DEFAULT now(),
  CONSTRAINT sessions_pkey PRIMARY KEY (id),
  CONSTRAINT sessions_token_key UNIQUE (token)
);

CREATE TABLE public.exam_attempts (
  id integer DEFAULT nextval('exam_attempts_id_seq'::regclass) NOT NULL,
  student_id integer,
  exam_id integer,
  attempt_number integer DEFAULT 1,
  status character varying DEFAULT 'in_progress'::character varying,
  start_time timestamp without time zone,
  end_time timestamp without time zone,
  CONSTRAINT exam_attempts_pkey PRIMARY KEY (id)
);

CREATE TABLE public.results (
  id integer DEFAULT nextval('results_id_seq'::regclass) NOT NULL,
  student_id integer,
  exam_id integer,
  score integer DEFAULT 0,
  total_questions integer DEFAULT 0,
  correct_answers integer DEFAULT 0,
  incorrect_answers integer DEFAULT 0,
  unanswered_questions integer DEFAULT 0,
  max_score integer DEFAULT 0,
  percentage numeric DEFAULT 0,
  grade character varying,
  time_taken_minutes integer DEFAULT 0,
  completed_at timestamp without time zone DEFAULT now(),
  CONSTRAINT results_pkey PRIMARY KEY (id)
);

CREATE TABLE public.responses (
  id integer DEFAULT nextval('responses_id_seq'::regclass) NOT NULL,
  result_id integer,
  exam_id integer,
  question_id integer,
  given_answer text,
  correct_answer text,
  is_correct boolean DEFAULT false,
  marks_obtained numeric DEFAULT 0,
  question_type character varying,
  is_attempted boolean DEFAULT false,
  CONSTRAINT responses_pkey PRIMARY KEY (id)
);

CREATE TABLE public.login_attempts (
  id integer DEFAULT nextval('login_attempts_id_seq'::regclass) NOT NULL,
  identifier character varying NOT NULL,
  ip_address character varying NOT NULL,
  failed_count integer DEFAULT 0,
  first_failed_at timestamp without time zone DEFAULT now(),
  last_failed_at timestamp without time zone DEFAULT now(),
  blocked_until timestamp without time zone,
  CONSTRAINT login_attempts_pkey PRIMARY KEY (id)
);

CREATE TABLE public.pw_tokens (
  id integer DEFAULT nextval('pw_tokens_id_seq'::regclass) NOT NULL,
  token character varying NOT NULL,
  email character varying NOT NULL,
  expires_at timestamp without time zone NOT NULL,
  used boolean DEFAULT false,
  created_at timestamp without time zone DEFAULT now(),
  type character varying,
  CONSTRAINT pw_tokens_pkey PRIMARY KEY (id),
  CONSTRAINT pw_tokens_token_key UNIQUE (token)
);

CREATE TABLE public.requests_raised (
  request_id integer DEFAULT nextval('requests_raised_request_id_seq'::regclass) NOT NULL,
  username character varying,
  email character varying,
  current_access character varying,
  requested_access character varying,
  request_date timestamp without time zone DEFAULT now(),
  request_status character varying DEFAULT 'pending'::character varying,
  reason text,
  processed_by character varying,
  processed_date timestamp without time zone,
  CONSTRAINT requests_raised_pkey PRIMARY KEY (request_id)
);

CREATE TABLE public.ai_chat_history (
  id integer DEFAULT nextval('ai_chat_history_id_seq'::regclass) NOT NULL,
  user_id integer,
  message text NOT NULL,
  is_user boolean DEFAULT true,
  "timestamp" timestamp without time zone DEFAULT now(),
  CONSTRAINT ai_chat_history_pkey PRIMARY KEY (id)
);

CREATE TABLE public.ai_usage_tracking (
  id integer DEFAULT nextval('ai_usage_tracking_id_seq'::regclass) NOT NULL,
  user_id integer,
  date date DEFAULT CURRENT_DATE,
  questions_used integer DEFAULT 0,
  CONSTRAINT ai_usage_tracking_pkey PRIMARY KEY (id)
);

CREATE TABLE public.chat_connections (
  id integer DEFAULT nextval('chat_connections_id_seq'::regclass) NOT NULL,
  requester_id integer NOT NULL,
  recipient_id integer NOT NULL,
  status character varying DEFAULT 'pending'::character varying,
  created_at timestamp without time zone DEFAULT now(),
  updated_at timestamp without time zone DEFAULT now(),
  CONSTRAINT chat_connections_pkey PRIMARY KEY (id)
);

CREATE TABLE public.chat_conversations (
  id integer DEFAULT nextval('chat_conversations_id_seq'::regclass) NOT NULL,
  is_group boolean DEFAULT false,
  group_name character varying,
  created_by integer,
  created_at timestamp without time zone DEFAULT now(),
  CONSTRAINT chat_conversations_pkey PRIMARY KEY (id)
);

CREATE TABLE public.chat_members (
  id integer DEFAULT nextval('chat_members_id_seq'::regclass) NOT NULL,
  conversation_id integer NOT NULL,
  user_id integer NOT NULL,
  joined_at timestamp without time zone DEFAULT now(),
  role character varying DEFAULT 'member'::character varying,
  CONSTRAINT chat_members_pkey PRIMARY KEY (id)
);

CREATE TABLE public.chat_messages (
  id integer DEFAULT nextval('chat_messages_id_seq'::regclass) NOT NULL,
  conversation_id integer NOT NULL,
  sender_id integer NOT NULL,
  sender_name character varying NOT NULL,
  message text NOT NULL,
  is_deleted boolean DEFAULT false,
  created_at timestamp without time zone DEFAULT now(),
  is_edited boolean DEFAULT false,
  reply_to_id integer,
  reply_to_text character varying,
  reply_to_name character varying,
  CONSTRAINT chat_messages_pkey PRIMARY KEY (id)
);

CREATE TABLE public.chat_unread (
  id integer DEFAULT nextval('chat_unread_id_seq'::regclass) NOT NULL,
  user_id integer NOT NULL,
  conversation_id integer NOT NULL,
  count integer DEFAULT 0,
  CONSTRAINT chat_unread_pkey PRIMARY KEY (id)
);

CREATE TABLE public.chat_visibility (
  id integer NOT NULL,
  user_id integer NOT NULL,
  conversation_id integer NOT NULL,
  cleared_at timestamp without time zone DEFAULT now() NOT NULL,
  CONSTRAINT chat_visibility_pkey PRIMARY KEY (id)
);

CREATE TABLE public.discussion_counts (
  question_id integer NOT NULL,
  count integer DEFAULT 0,
  CONSTRAINT discussion_counts_pkey PRIMARY KEY (question_id)
);

CREATE TABLE public.question_discussions (
  id integer DEFAULT nextval('question_discussions_id_seq'::regclass) NOT NULL,
  question_id integer NOT NULL,
  exam_id integer,
  user_id integer NOT NULL,
  username character varying NOT NULL,
  message text NOT NULL,
  parent_id integer,
  is_pinned boolean DEFAULT false,
  is_best_answer boolean DEFAULT false,
  is_deleted boolean DEFAULT false,
  is_edited boolean DEFAULT false,
  created_at timestamp without time zone DEFAULT now(),
  updated_at timestamp without time zone DEFAULT now(),
  CONSTRAINT question_discussions_pkey PRIMARY KEY (id)
);

-- ============================================================
-- STEP 5: CREATE INDEXES (non-primary, non-unique)
-- ============================================================

-- ai_chat_history
CREATE INDEX idx_ai_chat_user_id ON public.ai_chat_history USING btree (user_id);

-- ai_usage_tracking
CREATE INDEX idx_ai_usage_user_date ON public.ai_usage_tracking USING btree (user_id, date);

-- exam_attempts
CREATE INDEX idx_attempts_status ON public.exam_attempts USING btree (status);
CREATE INDEX idx_attempts_student_exam ON public.exam_attempts USING btree (student_id, exam_id);

-- exams
CREATE INDEX idx_exam_category_id ON public.exams USING btree (category_id);

-- login_attempts
CREATE INDEX idx_login_attempts_identifier ON public.login_attempts USING btree (identifier, ip_address);

-- questions
CREATE INDEX idx_questions_exam_id ON public.questions USING btree (exam_id);

-- responses
CREATE INDEX idx_responses_exam_id ON public.responses USING btree (exam_id);
CREATE INDEX idx_responses_question_id ON public.responses USING btree (question_id);
CREATE INDEX idx_responses_result_id ON public.responses USING btree (result_id);

-- results
CREATE INDEX idx_results_completed_at ON public.results USING btree (completed_at DESC);
CREATE INDEX idx_results_exam_id ON public.results USING btree (exam_id);
CREATE INDEX idx_results_student_id ON public.results USING btree (student_id);

-- sessions (partial indexes — only active sessions)
CREATE INDEX idx_sessions_token ON public.sessions USING btree (token) WHERE (active = true);
CREATE INDEX idx_sessions_user_id ON public.sessions USING btree (user_id) WHERE (active = true);

-- ============================================================
-- DONE! Schema migration complete.
-- ============================================================
```
