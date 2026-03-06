-- ============================================================
-- Secretaria — Supabase schema
-- Run this in the Supabase SQL Editor
-- ============================================================

-- Organizations
create table if not exists organizations (
  id           uuid primary key default gen_random_uuid(),
  owner_id     uuid references auth.users(id) on delete cascade,
  name         text not null,
  sector       text,
  phone        text,
  trial_ends   timestamptz default (now() + interval '14 days'),
  plan         text default 'trial',
  created_at   timestamptz default now()
);

-- Calls
create table if not exists calls (
  id               text primary key,
  org_id           uuid references organizations(id) on delete cascade,
  caller_number    text not null,
  caller_name      text,
  duration_seconds integer default 0,
  started_at       timestamptz not null,
  status           text not null check (status in ('completed','failed','ongoing')),
  sentiment        text not null check (sentiment in ('positive','neutral','negative')),
  summary          text,
  transcript       jsonb default '[]',
  sector           text,
  created_at       timestamptz default now()
);

-- Call actions (email sent, calendar event, task created)
create table if not exists call_actions (
  id           text primary key,
  call_id      text references calls(id) on delete cascade,
  org_id       uuid references organizations(id) on delete cascade,
  type         text not null check (type in ('email','calendar_event','task')),
  label        text,
  detail       text,
  status       text not null check (status in ('success','failed','pending')),
  error        text,
  executed_at  timestamptz default now()
);

-- Tasks
create table if not exists tasks (
  id           text primary key,
  org_id       uuid references organizations(id) on delete cascade,
  title        text not null,
  status       text not null default 'todo' check (status in ('todo','in_progress','done')),
  priority     text not null default 'medium' check (priority in ('high','medium','low')),
  due_date     date,
  caller_name  text,
  caller_phone text,
  notes        text,
  call_id      text references calls(id) on delete set null,
  created_at   timestamptz default now()
);

-- OAuth integrations (Gmail, Calendar)
create table if not exists integrations (
  id            uuid primary key default gen_random_uuid(),
  org_id        uuid references organizations(id) on delete cascade,
  provider      text not null,         -- 'gmail' | 'calendar'
  access_token  text not null,         -- AES-256-GCM encrypted
  refresh_token text not null,
  token_expiry  timestamptz,
  connected_at  timestamptz default now(),
  unique (org_id, provider)
);

-- ── Row-Level Security ────────────────────────────────────────

alter table organizations enable row level security;
alter table calls          enable row level security;
alter table call_actions   enable row level security;
alter table tasks          enable row level security;
alter table integrations   enable row level security;

-- Organizations: owner only
create policy "org_owner" on organizations
  for all using (owner_id = auth.uid());

-- Calls: via org membership
create policy "calls_org" on calls
  for all using (
    org_id in (select id from organizations where owner_id = auth.uid())
  );

create policy "call_actions_org" on call_actions
  for all using (
    org_id in (select id from organizations where owner_id = auth.uid())
  );

create policy "tasks_org" on tasks
  for all using (
    org_id in (select id from organizations where owner_id = auth.uid())
  );

create policy "integrations_org" on integrations
  for all using (
    org_id in (select id from organizations where owner_id = auth.uid())
  );

-- ── Indexes ───────────────────────────────────────────────────

create index if not exists calls_org_started    on calls(org_id, started_at desc);
create index if not exists tasks_org_status     on tasks(org_id, status);
create index if not exists call_actions_call    on call_actions(call_id);
