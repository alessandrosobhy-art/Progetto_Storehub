ALTER TABLE public.profiles
ADD COLUMN IF NOT EXISTS theme_key text NOT NULL DEFAULT 'base';

UPDATE public.profiles
SET theme_key = 'base'
WHERE theme_key IS NULL OR btrim(theme_key) = '';
