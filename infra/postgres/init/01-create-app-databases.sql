SELECT 'CREATE DATABASE agentforge'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'agentforge')\gexec

SELECT 'CREATE DATABASE agentforge_test'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'agentforge_test')\gexec

SELECT 'CREATE DATABASE agentforge_inbox'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'agentforge_inbox')\gexec

SELECT 'CREATE DATABASE agentforge_inbox_test'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'agentforge_inbox_test')\gexec
