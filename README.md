# Riwi Co. Internal Messaging Platform
*A secure, organized, and AI-powered messaging system for internal communication with strict access control.*

---

## Features
#### **User & Message Management**
- Create, edit, and delete users/messages (with original state preservation on failure).
- Track read status, search conversations, and query an AI copilot.

#### **AI Copilot (RAG-based)**
- Retrieves **only authorized** messages (no global access).
- Cites sources, handles insufficient context, and respects user permissions.
- Interchangeable AI providers (OpenAI SDK compatible).

#### **Security & Access Control**
- **Row-Level Security (RLS)** in PostgreSQL (no `BYPASSRLS`).
- JWT authentication (short-lived access tokens + refresh token rotation).
- Strict permission validation (DB-level + API).

####  **Backend & Database**
- **PostgreSQL 15+** with normalized schema (1NF–3NF), `timestamptz` (UTC), and constraints (`PK`, `FK`, `CHECK`, `UNIQUE`).
- **Clean Architecture** (SOLID principles, layered dependencies).
- **Keyset pagination** (no `OFFSET`), no physical message deletion, no SQL string concatenation.

#### **Frontend**
- Responsive UI (mobile/desktop) with **3 zones**: conversations, copilot panel, user profile.
- Lazy-loaded chat history (preserves scroll position).
- Multi-language support (ES/EN).

#### **Search & Vector DB**
- **Embeddings** for semantic search (consistent via triggers).
- **Highlighted search terms** in results.

#### **Deployment**
- Dockerized (`docker compose up` for DB, backend, frontend).
- Documented migration commands + `.env.example`.

---

## Technical Requirements

### Database (PostgreSQL)
- **Schema**: `bd_nombre_apellido_clan` (tables/columns prefixed with `rw_`).
- **Modeling**:
  - ER diagram with entities, attributes, cardinalities, and normalization (1NF–3NF).
  - `seed.json` corpus (entities, relationships, business rules).
- **Constraints**:
  - `PK`, `FK` (with `ON DELETE` justified), `UNIQUE`, `NOT NULL`, `CHECK`.
  - At least **1 partial unique index**.
- **Logic**:
  - Transactional functions (atomic operations).
  - **RLS policies** (actor set via `app.current_user_id`).
  - **Stored procedures**: User CRUD, conversation views.
- **Queries**:
  - Keyset-paginated message history.
  - Search with term highlighting.
  - Context retrieval for copilot (permission-aware).
  - AI usage analytics per user.

### Backend (Clean Architecture)
- **Layers**: Domain (core), Application (use cases), Infrastructure (DB/API).
- **API**: RESTful with:
  - Correct HTTP status codes.
  - Uniform error handling + correlation IDs.
  - Keyset pagination.
- **Security**:
  - Password hashing (bcrypt/Argon2).
  - JWT tokens (access + refresh rotation).
  - User ID **only** from token (never request body).

### Frontend
- **UI Components**:
  - Conversation thread (pending/sent/failed states).
  - Copilot panel (RAG responses with citations).
  - User profile (auth context).
- **Performance**:
  - Lazy loading, scroll position preservation.
  - Loading/empty/error states.

### AI Copilot
- **RAG Pipeline**:
  - Vector DB for embeddings (trigger-maintained consistency).
  - Context filtered by **user permissions**.
- **Behavior**:
  - Versioned system prompt.
  - Explicit denials for lacks of permission/scope/context.

### QA & Deployment
- **Tests**:
  - 2+ PostgreSQL tests (e.g., reject non-member access, private channel isolation).
- **Evidences**:
  - 5-min video/demo: Login → message → search → copilot query (with citations) → permission denial.
- **Deployment**:
  - `docker compose up` (full stack).
  - Migration script + `.env.example`.

---

## Getting Started
1. **Clone & Configure**:
   ```bash
   git clone https://github.com/SrLampi1001/employment_assessment_test.git
   cp .env.example .env
   ```
2. **Add your own credentials**:
    <!-- Placeholder for actual .env.exmaple and required params -->
3. **Run**:
   ```bash
   docker compose up -d
   # Apply migrations & seed data
    docker compose run migrate
   ```
4. **Access**:
   - Frontend: `http://localhost:3000` — The port can change based on your own .env configuration
   - API Docs: `http://localhost:3000/api/docs` — The port can change based on your own .env configuration

---

## Project Structure
```
.
├── /backend          # Clean Architecture layers
├── /frontend         # React/Vue/Svelte (responsive)
├── /db               # PostgreSQL + migrations
├── /docker           # Compose files
├── README.md         # This file
└── .env.example      # Environment template
```

---

## Security Notes
- **Never** bypass RLS (`BYPASSRLS` forbidden).
- **All queries** must use parameterized statements (no SQL injection).
- **AI Context**: Strictly scoped to user’s accessible channels.

