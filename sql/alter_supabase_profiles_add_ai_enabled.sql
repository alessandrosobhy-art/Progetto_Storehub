ALTER TABLE public.profiles
ADD COLUMN IF NOT EXISTS ai_enabled boolean NOT NULL DEFAULT false;

UPDATE public.profiles
SET ai_enabled = true
WHERE lower(coalesce(role, '')) = 'admin';
