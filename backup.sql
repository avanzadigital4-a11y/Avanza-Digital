--
-- PostgreSQL database dump
--

\restrict kjDmqapruizoBVvsAbcv0ekQptnliI2R9f0Mov1q8of8yt4ntHVCbR0mNHickPE

-- Dumped from database version 18.3 (Debian 18.3-1.pgdg13+1)
-- Dumped by pg_dump version 18.3

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: admins; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.admins (
    id integer NOT NULL,
    username character varying,
    password_hash character varying,
    creado_en timestamp without time zone
);


ALTER TABLE public.admins OWNER TO postgres;

--
-- Name: admins_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.admins_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.admins_id_seq OWNER TO postgres;

--
-- Name: admins_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.admins_id_seq OWNED BY public.admins.id;


--
-- Name: aliados; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.aliados (
    id integer NOT NULL,
    codigo character varying,
    nombre character varying NOT NULL,
    dni character varying,
    email character varying,
    whatsapp character varying,
    ciudad character varying,
    perfil character varying,
    fecha_firma character varying,
    nivel character varying,
    password_hash character varying,
    activo boolean,
    ref_code character varying,
    creado_en timestamp without time zone,
    cantidad_logins integer DEFAULT 0,
    ultimo_login timestamp without time zone,
    sponsor_id integer,
    onboarding_completado boolean DEFAULT false,
    reputacion_score integer DEFAULT 50,
    badges text DEFAULT '[]'::text,
    reputacion_calculada_en timestamp without time zone,
    creditos integer DEFAULT 0,
    portal_publico_activo boolean DEFAULT true,
    portal_publico_titular character varying,
    portal_publico_bio text
);


ALTER TABLE public.aliados OWNER TO postgres;

--
-- Name: aliados_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.aliados_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.aliados_id_seq OWNER TO postgres;

--
-- Name: aliados_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.aliados_id_seq OWNED BY public.aliados.id;


--
-- Name: auditorias_log; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.auditorias_log (
    id integer NOT NULL,
    aliado_id integer,
    ref_code character varying,
    dominio character varying,
    score integer,
    email_capturado character varying,
    creado_en timestamp without time zone
);


ALTER TABLE public.auditorias_log OWNER TO postgres;

--
-- Name: auditorias_log_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.auditorias_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.auditorias_log_id_seq OWNER TO postgres;

--
-- Name: auditorias_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.auditorias_log_id_seq OWNED BY public.auditorias_log.id;


--
-- Name: automation_log; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.automation_log (
    id integer NOT NULL,
    prospecto_id integer,
    aliado_id integer,
    paso integer NOT NULL,
    canal character varying,
    asunto character varying,
    mensaje text,
    exitoso boolean,
    creado_en timestamp without time zone
);


ALTER TABLE public.automation_log OWNER TO postgres;

--
-- Name: automation_log_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.automation_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.automation_log_id_seq OWNER TO postgres;

--
-- Name: automation_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.automation_log_id_seq OWNED BY public.automation_log.id;


--
-- Name: bolsa_leads; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.bolsa_leads (
    id integer NOT NULL,
    empresa character varying NOT NULL,
    rubro character varying NOT NULL,
    telefono character varying NOT NULL,
    email character varying,
    estado character varying,
    aliado_id integer,
    fecha_carga timestamp without time zone,
    fecha_reclamo timestamp without time zone,
    resultado character varying,
    notif_24h_enviada boolean DEFAULT false,
    nombre_contacto character varying,
    whatsapp character varying,
    instagram character varying,
    facebook character varying,
    web character varying,
    horario character varying,
    rating character varying,
    resenas character varying,
    extra text,
    tier character varying DEFAULT 'basico'::character varying,
    costo_creditos integer DEFAULT 0,
    score_calidad integer DEFAULT 50,
    notas_calificacion text
);


ALTER TABLE public.bolsa_leads OWNER TO postgres;

--
-- Name: bolsa_leads_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.bolsa_leads_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.bolsa_leads_id_seq OWNER TO postgres;

--
-- Name: bolsa_leads_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.bolsa_leads_id_seq OWNED BY public.bolsa_leads.id;


--
-- Name: comunidad_comentarios; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.comunidad_comentarios (
    id integer NOT NULL,
    post_id integer,
    aliado_id integer,
    cuerpo text NOT NULL,
    creado_en timestamp without time zone
);


ALTER TABLE public.comunidad_comentarios OWNER TO postgres;

--
-- Name: comunidad_comentarios_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.comunidad_comentarios_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.comunidad_comentarios_id_seq OWNER TO postgres;

--
-- Name: comunidad_comentarios_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.comunidad_comentarios_id_seq OWNED BY public.comunidad_comentarios.id;


--
-- Name: comunidad_posts; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.comunidad_posts (
    id integer NOT NULL,
    aliado_id integer,
    tipo character varying,
    titulo character varying NOT NULL,
    cuerpo text NOT NULL,
    likes integer,
    fijado boolean,
    oculto boolean,
    creado_en timestamp without time zone
);


ALTER TABLE public.comunidad_posts OWNER TO postgres;

--
-- Name: comunidad_posts_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.comunidad_posts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.comunidad_posts_id_seq OWNER TO postgres;

--
-- Name: comunidad_posts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.comunidad_posts_id_seq OWNED BY public.comunidad_posts.id;


--
-- Name: prospectos; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.prospectos (
    id integer NOT NULL,
    aliado_id integer,
    nombre character varying NOT NULL,
    contacto character varying,
    plan_interes character varying,
    estado character varying,
    nota text,
    interesante boolean,
    fecha_contacto timestamp without time zone,
    fecha_respuesta timestamp without time zone,
    creado_en timestamp without time zone,
    rubro character varying,
    tamano character varying,
    urgencia character varying,
    score_ia integer DEFAULT 0,
    plan_recomendado character varying,
    pitch_sugerido text,
    perfilado_en timestamp without time zone,
    automation_paso integer DEFAULT 0,
    automation_ultimo_en timestamp without time zone,
    automation_activa_desde timestamp without time zone
);


ALTER TABLE public.prospectos OWNER TO postgres;

--
-- Name: prospectos_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.prospectos_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.prospectos_id_seq OWNER TO postgres;

--
-- Name: prospectos_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.prospectos_id_seq OWNED BY public.prospectos.id;


--
-- Name: referidos; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.referidos (
    id integer NOT NULL,
    aliado_id integer,
    nombre_cliente character varying NOT NULL,
    plan_elegido character varying NOT NULL,
    notas text,
    registrado_en timestamp without time zone,
    acuse_recibo boolean,
    convertido boolean
);


ALTER TABLE public.referidos OWNER TO postgres;

--
-- Name: referidos_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.referidos_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.referidos_id_seq OWNER TO postgres;

--
-- Name: referidos_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.referidos_id_seq OWNED BY public.referidos.id;


--
-- Name: transacciones_credito; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.transacciones_credito (
    id integer NOT NULL,
    aliado_id integer,
    delta integer NOT NULL,
    motivo character varying NOT NULL,
    referencia character varying,
    creado_en timestamp without time zone
);


ALTER TABLE public.transacciones_credito OWNER TO postgres;

--
-- Name: transacciones_credito_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.transacciones_credito_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.transacciones_credito_id_seq OWNER TO postgres;

--
-- Name: transacciones_credito_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.transacciones_credito_id_seq OWNED BY public.transacciones_credito.id;


--
-- Name: ventas; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.ventas (
    id integer NOT NULL,
    aliado_id integer,
    referido_id integer,
    nombre_cliente character varying NOT NULL,
    plan character varying NOT NULL,
    valor_usd double precision NOT NULL,
    comision_pct double precision NOT NULL,
    comision_usd double precision NOT NULL,
    confirmada boolean,
    pagada boolean,
    fecha_venta timestamp without time zone,
    fecha_pago timestamp without time zone,
    modalidad_pago character varying,
    notas text,
    creado_en timestamp without time zone,
    cuotas integer DEFAULT 1,
    financiacion_pct double precision DEFAULT 0.0
);


ALTER TABLE public.ventas OWNER TO postgres;

--
-- Name: ventas_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.ventas_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.ventas_id_seq OWNER TO postgres;

--
-- Name: ventas_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.ventas_id_seq OWNED BY public.ventas.id;


--
-- Name: admins id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.admins ALTER COLUMN id SET DEFAULT nextval('public.admins_id_seq'::regclass);


--
-- Name: aliados id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.aliados ALTER COLUMN id SET DEFAULT nextval('public.aliados_id_seq'::regclass);


--
-- Name: auditorias_log id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.auditorias_log ALTER COLUMN id SET DEFAULT nextval('public.auditorias_log_id_seq'::regclass);


--
-- Name: automation_log id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.automation_log ALTER COLUMN id SET DEFAULT nextval('public.automation_log_id_seq'::regclass);


--
-- Name: bolsa_leads id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.bolsa_leads ALTER COLUMN id SET DEFAULT nextval('public.bolsa_leads_id_seq'::regclass);


--
-- Name: comunidad_comentarios id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.comunidad_comentarios ALTER COLUMN id SET DEFAULT nextval('public.comunidad_comentarios_id_seq'::regclass);


--
-- Name: comunidad_posts id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.comunidad_posts ALTER COLUMN id SET DEFAULT nextval('public.comunidad_posts_id_seq'::regclass);


--
-- Name: prospectos id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.prospectos ALTER COLUMN id SET DEFAULT nextval('public.prospectos_id_seq'::regclass);


--
-- Name: referidos id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.referidos ALTER COLUMN id SET DEFAULT nextval('public.referidos_id_seq'::regclass);


--
-- Name: transacciones_credito id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.transacciones_credito ALTER COLUMN id SET DEFAULT nextval('public.transacciones_credito_id_seq'::regclass);


--
-- Name: ventas id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ventas ALTER COLUMN id SET DEFAULT nextval('public.ventas_id_seq'::regclass);


--
-- Data for Name: admins; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.admins (id, username, password_hash, creado_en) FROM stdin;
1	ivan	$2b$12$/lltgkkyl3mpZI3zYtjYn.Nz0n7fBB7hPkOMf/XwzZwTsaEiotZ4C	2026-03-23 20:00:45.338017
\.


--
-- Data for Name: aliados; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.aliados (id, codigo, nombre, dni, email, whatsapp, ciudad, perfil, fecha_firma, nivel, password_hash, activo, ref_code, creado_en, cantidad_logins, ultimo_login, sponsor_id, onboarding_completado, reputacion_score, badges, reputacion_calculada_en, creditos, portal_publico_activo, portal_publico_titular, portal_publico_bio) FROM stdin;
13	AL-013	Guillermo Santellan	20240319528	guillermosantellan3@gmail.com	3855101222	Santiago del Estero	Closer de elite	19/03/2026	BASIC	$2b$12$lBnDy3fFP5g5wk8vw4J0COF2VeQLg1Ow9CpEcWy8W2/jDXXWGkx3i	t	guille3316	2026-03-20 19:56:04.250076	2	2026-04-11 22:25:25.319838	\N	f	50	[]	\N	0	t	\N	\N
2	AL-002	Maximiliano Lionel Villafañe	48.121.122	maximovillafane@gmail.com	+54 9 3813 658051	San Miguel de Tucumán	Generación de clientes	12/03/2026	BASIC	$2b$12$7jCFUjKPiB.plB0wQaqVg.BPOLIBkEcaYC8H4oWzy5JfMVh9kDsNq	t	maximi8322	2026-03-20 19:55:52.94986	3	2026-04-11 02:17:33.470459	\N	f	50	[]	\N	0	t	\N	\N
8	AL-008	Axel Amieva	39393769	axelamieva@avanza.ref				17/03/2026	BASIC	$2b$12$0Ko8LYiWTpstFwaBG.CuRe0Amy8aCgmCxtLc4b/CgXR/o0j.SZBP2	t	axel0980	2026-03-20 19:55:58.990334	1	2026-04-05 00:32:44.630093	\N	f	50	[]	\N	0	t	\N	\N
32	AL-022	Kelvis Enrique Escalona Piña	29742961	nose2				06/04/2026	BASIC	$2b$12$ycLLjPMIPJydfkePQqeOkewlCPPQLaFGWqx5nEDz5Kk2.wi1HGyuS	t	kelvis2418	2026-04-07 00:48:40.563702	5	2026-04-08 17:50:11.284783	\N	f	50	[]	\N	0	t	\N	\N
21	AL-021	Patricio Daniel Gutierrez	20291777032	patopaint3@gmail.com	3888488973	jujuy	comerciante	01/04/2026	BASIC	$2b$12$lK.63Vq2cdz.XhPatuYlJe8MzQhU3xb6l/rdp2D9XVmzD8ZIZS55K	t	patric0070	2026-04-01 13:38:32.961787	5	2026-04-07 16:51:13.235577	\N	f	50	[]	\N	0	t	\N	\N
4	AL-004	Suarez Alejandro M	34092505	suarezalejandro@avanza.ref				16/03/2026	BASIC	$2b$12$p6X0Iz1UuNrZ1tCs5enWuumwuh4lIdNDHiAfTgdGCC7GcYuuHKTqu	f	suarez7060	2026-03-20 19:55:54.996184	0	\N	\N	f	50	[]	\N	0	t	\N	\N
3	AL-003	Alejandro Ezequiel Vyhñak	48576415	avyhnak@gmail.com	+54 911 32374824	Ramos Mejía, Buenos Aires	Estudiante universitario	16/03/2026	BASIC	$2b$12$aFBkZTgXgzLm0YBbDU7LMO8D6GaFTwtVi9VyRVi/EHCLIUGwbDPrK	t	alejan7851	2026-03-20 19:55:53.970928	1	2026-04-11 17:29:39.479786	\N	f	50	[]	\N	0	t	\N	\N
5	AL-005	Vera Diego Ezequiel	43747608	veraaeze@gmail.com	+54 9 3795 024193	Corrientes Capital		17/03/2026	BASIC	$2b$12$R6Y/bFvmfd.XCOnv1UNrieAIDWyQurmWxIN9ixatbLOXvJFksPe7K	f	vera7251	2026-03-20 19:55:56.001733	0	\N	\N	f	50	[]	\N	0	t	\N	\N
15	AL-015	Maximiliano Ezequiel Torrez	20-39941229-4	ezequiel.closer.ventas@gmail.com	2302694127	La Pampa		19/03/2026	BASIC	$2b$12$OKDyr.DDoASWFxsxRXmyw.aGRM5eGRm7nYZb.E68PZfVv6Bi/OHQi	f	maximi0673	2026-03-20 19:56:06.332514	0	\N	\N	f	50	[]	\N	0	t	\N	\N
16	AL-016	Fabio Chasco	14.526754	fabiochasco@gmail.com	+54 9 1135522642	Banfield, buenos aires	Ingeniero Industrial	24/3/2026	BASIC	$2b$12$RUT4yR4Ah8v.jKUsewxgmebl3ybMNmYqEidnNxLDITVl6j7T/uxsy	f	fabio7682	2026-03-24 19:41:20.031187	0	\N	\N	f	50	[]	\N	0	t	\N	\N
20	AL-020	Julian yugneivich	40145989	Julianyugneivich@gmail.com	+54 9 3487 31 1200	zarate, buenos aires		31/03/2026	BASIC	$2b$12$YO51bI2XYaINu.cd3CDSgOlqc1qM/t4tVHcboTe0FNC93e.yRrlaC	t	julian7215	2026-03-31 23:12:32.333089	1	2026-04-11 21:31:57.907396	\N	f	50	[]	\N	0	t	\N	\N
34	AL-023	Alexis Lumpuy Pupo	05012173026	closerbinance@gmail.com	+5363236067	Sancti Spiritus	Closer de Ventas	09/04/2026	BASIC	$2b$12$BIwAJe/5QB7UVkcKvxTEE.hby9ze7MU3jfdwVkw/PzY6gFbPhxIpu	t	alexis4974	2026-04-09 18:12:32.716414	5	2026-04-10 19:09:08.375021	\N	f	50	[]	\N	0	t	\N	\N
7	AL-007	Kevin David Celiz	41564930	celizdavid86@gmail.com		Buenos Aires	Generación de leads y ventas digitales	17/03/2026	BASIC	$2b$12$ndSWOUGR7x79AHuUDTamhelWrLdZrrK3vLoSyKoNdX6wZTyEqYBli	f	kevin9139	2026-03-20 19:55:57.964945	0	\N	\N	f	50	[]	\N	0	t	\N	\N
17	AL-017	Ignacio saenz	20-46511055-5	saenzignacio15@gmail.com	(0358) 424-6925	Rio Cuarto (Cordoba)	Closer de Venta	25/03/2026	BASIC	$2b$12$1Z9TtUsoOtkbBCYdncIXy.AZsGBPRgr7hUy4ZWaVqfx3ABAXIyEKy	t	ignaci9130	2026-03-25 22:27:02.707131	4	2026-04-08 16:43:29.255014	\N	f	50	[]	\N	0	t	\N	\N
9	AL-009	Marco Alexander Cáceres García	20-95758318-1	marcoalex270@gmail.com	+34614587345	Buenos Aires	Técnico en administración	17/03/2026	BASIC	$2b$12$iFxOjgQnw303NuvzIg.wDe9Nro44fG7OjBTCkJ3r/TXCvgug6JEGS	f	marco1800	2026-03-20 19:56:00.014899	0	\N	\N	f	50	[]	\N	0	t	\N	\N
10	AL-010	Lucas Lopez	50824332	lucaslopez20117@gmail.com		Buenos Aires		19/03/2026	BASIC	$2b$12$T56LR/CMKhUpioPvXub3IOfn3TCjwG3CMdLfJfLHf6mwYrYWzUj76	f	lucas4928	2026-03-20 19:56:01.054878	0	\N	\N	f	50	[]	\N	0	t	\N	\N
11	AL-011	Leonel Martinez Mazzoconi	42454483	leonelmartinez@avanza.ref				20/03/2026	BASIC	$2b$12$EGVS7EXEVnl0jUQggpfMYO.JbgSRswkdl6yqvLnoOUiyHssdnbcGe	f	leonel4632	2026-03-20 19:56:02.114272	0	\N	\N	f	50	[]	\N	0	t	\N	\N
12	AL-012	Jose Angel Zambrano	27242828	jazsrm7@gmail.com	+584124555382	Venezuela, Estado Miranda, Cua	Socio estrategico	19/03/2026	BASIC	$2b$12$eVYCcFjwSLjJRoEcx9MDvOp2VeuFYTJKk9n9WWBqLUg.K9Kbh1WlO	f	jose3744	2026-03-20 19:56:03.179018	0	\N	\N	f	50	[]	\N	0	t	\N	\N
18	AL-018	Kevin De Caro Costa	20-48241770-2	kevindecaro364@gmail.com	+54 9 11 25480310	CABA, Buenos Aires, Argentina	Prospeccion y Generacion de Leads	29/03/2026	BASIC	$2b$12$lCNgGBnWpFi8TlPGpNL0veUMRxbYTAhXtXTFXmMU2DDvcksAD.32C	f	kevin0606	2026-03-29 20:05:54.969493	0	\N	\N	f	50	[]	\N	0	t	\N	\N
37	AL-026	ivan dario galarza		asdasadasdasd@gmail.com	3424392759	Ciudad de Santa Fe	Agencia B2B	19/04/2026	BASIC	$2b$12$kfDlkt9f0nzAteReynJ.5.fUwPJqExfFig5lT/26qmyxd2N7.7q.K	t	ivan4177	2026-04-19 01:26:25.398634	1	2026-04-19 01:32:52.399449	\N	f	40	[]	2026-04-19 01:32:53.050234	0	t	\N	\N
1	AL-001	Morena Alejandra Altamiranda Ganini	46.128.865	morealtamiranda24@gmail.com	+54 9 3548 414273	La Falda, Córdoba	Closer de ventas B2B	12/03/2026	BASIC	$2b$12$4.kSD8xN0gZedxH1GGvoK.tARNUvvHVXjT9S1WKl6zWgg451NzqbG	t	morena9516	2026-03-20 19:55:51.882934	11	2026-04-17 07:45:06.857433	\N	f	40	["FIEL"]	2026-04-17 07:45:07.422532	0	t	\N	\N
19	AL-019	Maria Duran Osorio	28719238	nose				31/03/2026	BASIC	$2b$12$okrJB5ctPVm8/fyfrDK0rer4k2bBbrY5gxxXpAO5Vr2ai0cw8GPwa	f	maria2989	2026-03-31 14:14:41.93636	0	\N	\N	f	50	[]	\N	0	t	\N	\N
35	AL-024	ivan g		ivamchocolon@gmail.com	3424392759	santa fe	Agencia B2B	18/04/2026	BASIC	$2b$12$w7U3pA/gSyUWL6q9vxNNCeHidK1.nXjDnByazrFpfhS1BLCvjacMS	f	ivan1893	2026-04-18 13:46:32.475717	3	2026-04-18 20:25:40.547034	\N	f	40	[]	2026-04-18 20:25:41.129912	0	t	\N	\N
36	AL-025	ivan dario galarza		galarzaivan0410@gmail.com	3424392759	Ciudad de Santa Fe	Agencia B2B	18/04/2026	BASIC	$2b$12$ahYbwsrY3WWZTrp5VXubkO8k9WTLYGNkreeM3rxXDt13XWinDc6i6	t	ivan5014	2026-04-18 18:25:02.648989	3	2026-04-18 18:50:31.316272	\N	f	40	[]	2026-04-18 18:50:31.856176	0	t	\N	\N
\.


--
-- Data for Name: auditorias_log; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.auditorias_log (id, aliado_id, ref_code, dominio, score, email_capturado, creado_en) FROM stdin;
1	1	morena9516	avanzadigital.digital	87		2026-04-03 16:42:18.772641
2	\N		avanzadigital.digital	83		2026-04-05 20:24:26.323815
\.


--
-- Data for Name: automation_log; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.automation_log (id, prospecto_id, aliado_id, paso, canal, asunto, mensaje, exitoso, creado_en) FROM stdin;
\.


--
-- Data for Name: bolsa_leads; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.bolsa_leads (id, empresa, rubro, telefono, email, estado, aliado_id, fecha_carga, fecha_reclamo, resultado, notif_24h_enviada, nombre_contacto, whatsapp, instagram, facebook, web, horario, rating, resenas, extra, tier, costo_creditos, score_calidad, notas_calificacion) FROM stdin;
2	Mas HerreríaCórdoba 2836, SF	METALÚRGICAS — Nuevas	+54 342 409-0357		disponible	\N	2026-04-11 02:14:57.453603	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
3	Baccega Hermanos4 de Enero 6199, SF	METALÚRGICAS — Nuevas	+54 342 655-2049		disponible	\N	2026-04-11 02:14:58.076288	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
4	Blacksmithing WorkshopC.18 3423, Sauce Viejo	METALÚRGICAS — Nuevas	+54 9 342 566-7808		disponible	\N	2026-04-11 02:14:58.626517	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
5	La Goma ArgentinaRivadavia 3100, SF (plástico/caucho)	Insumos IndustrialesJ.P. López 3155, SF	+54 342 453-2327		disponible	\N	2026-04-11 02:14:59.11213	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
6	Corralón DimarG. de Lamadrid 3035, SF	️ CONSTRUCCIÓN / FERRETERÍAS — Nuevas	+54 342 453-3152		disponible	\N	2026-04-11 02:14:59.593627	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
7	Corralón AvenidaAv. J. Gorriti 3109, SF	️ CONSTRUCCIÓN / FERRETERÍAS — Nuevas	+54 342 469-0955		disponible	\N	2026-04-11 02:15:00.078815	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
8	Santa Fe Materials S.A.Av. Blas Parera 7730, SF	️ CONSTRUCCIÓN / FERRETERÍAS — Nuevas	+54 342 488-4945		disponible	\N	2026-04-11 02:15:00.569723	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
9	Ferretería RafloAv. A. del Valle 4629, SF	️ CONSTRUCCIÓN / FERRETERÍAS — Nuevas	+54 342 455-1297		disponible	\N	2026-04-11 02:15:01.05387	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
10	Corralón GüemesGüemes 5339, SF	️ CONSTRUCCIÓN / FERRETERÍAS — Nuevas	+54 342 469-4097		disponible	\N	2026-04-11 02:15:01.547371	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
11	Corralón UBAzopardo 8825, SF	️ CONSTRUCCIÓN / FERRETERÍAS — Nuevas	+54 342 506-0569		disponible	\N	2026-04-11 02:15:02.097481	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
12	Ferretería y Corralón BeruttiBerutti, SF	️ CONSTRUCCIÓN / FERRETERÍAS — Nuevas	+54 342 488-6300		disponible	\N	2026-04-11 02:15:02.647897	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
13	Sandona Hnos.Saavedra 3350, SF	️ CONSTRUCCIÓN / FERRETERÍAS — Nuevas	+54 342 455-8969		disponible	\N	2026-04-11 02:15:03.136203	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
14	Transporte PedritoAv. Á. V. Peñaloza 6310, SF	LOGÍSTICA / TRANSPORTE — Nuevas	+54 342 489-0111		disponible	\N	2026-04-11 02:15:03.685337	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
15	Transporte LonderoSeguí 1784, SF	LOGÍSTICA / TRANSPORTE — Nuevas	+54 342 452-2513		disponible	\N	2026-04-11 02:15:04.176873	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
16	Transporte En RutaJunín 3537, SF	LOGÍSTICA / TRANSPORTE — Nuevas	+54 342 463-1124		disponible	\N	2026-04-11 02:15:04.661487	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
17	Transporte Snaider SRLAv. J. Gorriti 3260, SF	LOGÍSTICA / TRANSPORTE — Nuevas	+54 342 460-9700		disponible	\N	2026-04-11 02:15:05.142876	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
18	Fletes LeoM. de Polonia 3000, SF	LOGÍSTICA / TRANSPORTE — Nuevas	+54 342 445-6896		disponible	\N	2026-04-11 02:15:05.621467	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
19	Fletes y Mudanzas La Capital SFChubut 6065, SF	LOGÍSTICA / TRANSPORTE — Nuevas	+54 342 478-5356		disponible	\N	2026-04-11 02:15:06.166519	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
20	Fletes TransFeAzcuénaga 3400, SF	LOGÍSTICA / TRANSPORTE — Nuevas	+54 342 422-4104		disponible	\N	2026-04-11 02:15:06.652482	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
21	Carlos Benassi SRLFrancia 3776, SF	DISTRIBUIDORAS — Nuevas	+54 342 452-2487		disponible	\N	2026-04-11 02:15:07.13379	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
22	DDI Distribuidora del InteriorSan Lorenzo 1501, SF	DISTRIBUIDORAS — Nuevas	+54 342 459-5333		disponible	\N	2026-04-11 02:15:07.616651	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
23	El Buen Sabor DistribuidoraSaavedra 1101, SF	DISTRIBUIDORAS — Nuevas	+54 342 500-6285		disponible	\N	2026-04-11 02:15:08.309033	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
24	A.A.J. Art. Limpieza y PerfumeríaAv. Blas Parera 7203, SF	DISTRIBUIDORAS — Nuevas	+54 342 584-4764		disponible	\N	2026-04-11 02:15:08.792213	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
25	Mayorista Saphirus Santa FeAv. F. Zuviría 4822, SF	DISTRIBUIDORAS — Nuevas	+54 342 523-4216		disponible	\N	2026-04-11 02:15:09.353939	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
26	MLS RefrigeraciónE. Zeballos 4414, SF	SERVICIOS TÉCNICOS — Nuevos rubros	+54 342 584-7700		disponible	\N	2026-04-11 02:15:09.898728	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
27	Tellier RefrigeraciónAv. F. Zuviría 4138, SF	SERVICIOS TÉCNICOS — Nuevos rubros	+54 342 455-1199		disponible	\N	2026-04-11 02:15:10.382547	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
28	HETZER Refrigeración S.A.25 de Mayo 3775, SF	SERVICIOS TÉCNICOS — Nuevos rubros	+54 342 455-2277		disponible	\N	2026-04-11 02:15:10.857108	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
29	TecníFrío RefrigeraciónBv. Pellegrini 3247, SF	SERVICIOS TÉCNICOS — Nuevos rubros	+54 342 427-4027		disponible	\N	2026-04-11 02:15:11.491627	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
30	RP Servicios TécnicosPje. Quiroga 5066, SF	SERVICIOS TÉCNICOS — Nuevos rubros	+54 342 405-9447		disponible	\N	2026-04-11 02:15:11.977	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
31	Refrigeración Las HerasDelfín Huergo 1930, SF	SERVICIOS TÉCNICOS — Nuevos rubros	+54 9 342 630-9497		disponible	\N	2026-04-11 02:15:12.466112	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
32	Meyer RefrigeraciónGral. J. Lavalle 3716, SF	Servicios Integrales - AiresAlvear 3858, SF	+54 342 452-0769		disponible	\N	2026-04-11 02:15:12.955395	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
33	Metalcraft SRLAparicio Saravia 908	METALÚRGICAS	+54 341 670-7341		disponible	\N	2026-04-11 02:24:23.327432	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
34	Armetal SRLCafferata	METALÚRGICAS	+54 341 282-0072		disponible	\N	2026-04-11 02:24:23.784206	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
35	Metalúrgica RosarioMagallanes	METALÚRGICAS	+54 341 643-9468		disponible	\N	2026-04-11 02:24:24.309388	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
36	BERTOT METALMECÁNICA SRLCallao	METALÚRGICAS	+54 341 463-7573		disponible	\N	2026-04-11 02:24:24.848468	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
37	Metaltécnica SRL / AGROFLEXCol. Juan Pablo II	METALÚRGICAS	+54 341 361-1001		disponible	\N	2026-04-11 02:24:25.304738	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
38	Mtz MetalúrgicaPres. Quintana	METALÚRGICAS	+54 341 617-3272		disponible	\N	2026-04-11 02:24:25.761412	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
39	CSIngenieria SRLFrench	METALÚRGICAS	+54 341 457-6419		disponible	\N	2026-04-11 02:24:26.286233	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
40	Empresa MetalúrgicaFerreyra 873	METALÚRGICAS	+54 341 643-7556		disponible	\N	2026-04-11 02:24:26.8139	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
41	Integral Agropecuaria S.A.	AGRO	+54 341 561-1994		disponible	\N	2026-04-11 02:24:27.27326	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
42	ALZ AgroSanta Fe	AGRO	+54 341 530-0806		disponible	\N	2026-04-11 02:24:27.740614	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
43	Rosario Insumos Agropecuarios SA	AGRO	+54 341 691-2879		disponible	\N	2026-04-11 02:24:28.203177	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
44	Semillería Romero Desde	AGRO	+54 9 341 618-0274		disponible	\N	2026-04-11 02:24:28.666853	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
45	Corralón Victoria - Red Disensa	️ CONSTRUCCIÓN / CORRALONES	+54 341 583-5088		disponible	\N	2026-04-11 02:24:29.13315	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
46	Materiales Colombia SRLColombia 398	️ CONSTRUCCIÓN / CORRALONES	+54 341 642-7438		disponible	\N	2026-04-11 02:24:29.591372	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
47	Corralón Contino	️ CONSTRUCCIÓN / CORRALONES	+54 341 466-0522		disponible	\N	2026-04-11 02:24:30.06492	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
48	Materiales Avenida SRL	️ CONSTRUCCIÓN / CORRALONES	+54 341 643-7787		disponible	\N	2026-04-11 02:24:30.523858	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
49	Hipermercado de la Construcción	️ CONSTRUCCIÓN / CORRALONES	+54 341 451-6465		disponible	\N	2026-04-11 02:24:30.993082	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
50	Diagonal - Construction MaterialsRío Negro	️ CONSTRUCCIÓN / CORRALONES	+54 341 612-0671		disponible	\N	2026-04-11 02:24:31.455189	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
51	Corralón Phenomenon	️ CONSTRUCCIÓN / CORRALONES	+54 341 319-6163		disponible	\N	2026-04-11 02:24:31.918905	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
52	Luciani Materials S.R.L.	️ CONSTRUCCIÓN / CORRALONES	+54 223 651-8207		disponible	\N	2026-04-11 02:24:32.449719	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
53	Materiales Avellaneda	️ CONSTRUCCIÓN / CORRALONES	+54 341 689-2561		disponible	\N	2026-04-11 02:24:32.980066	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
58	Logística Cafferata IGaray	Centro de Encomiendas Santa FeRío de Janeiro 1937	+54 341 559-0171		disponible	\N	2026-04-11 02:24:35.57058	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
63	TYNA Mayorista	DISTRIBUIDORAS / MAYORISTAS	+54 341 422-0202		disponible	\N	2026-04-11 02:24:37.871857	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
68	La DistribuidoraSan Juan	Distribuidora MatienzoMatienzo 2643	+54 341 361-2661		disponible	\N	2026-04-11 02:24:40.314015	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
73	Etna ComputaciónCorrientes	SERVICIOS TÉCNICOS	+54 341 511-6010		disponible	\N	2026-04-11 02:24:42.622032	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
54	Transporte	LOGÍSTICA / TRANSPORTE	+54 341 466-7478		disponible	\N	2026-04-11 02:24:33.577016	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
59	Centro 27 - Sucursal RosarioSan Nicolás	Centro de Encomiendas Santa FeRío de Janeiro 1937	+54 341 545-5297		disponible	\N	2026-04-11 02:24:36.036042	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
64	America Distribuidora	DISTRIBUIDORAS / MAYORISTAS	+54 341 266-2000		disponible	\N	2026-04-11 02:24:38.338218	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
69	Appiano Mayorista de Alimentos	Distribuidora MatienzoMatienzo 2643	+54 341 355-9446		disponible	\N	2026-04-11 02:24:40.769315	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
74	SERVICEINFORMATICO®Santa Fe	SERVICIOS TÉCNICOS	+54 341 550-1090		disponible	\N	2026-04-11 02:24:43.086531	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
55	Rosario Logística SA	LOGÍSTICA / TRANSPORTE	+54 341 317-4646		disponible	\N	2026-04-11 02:24:34.105235	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
60	Estación Río de JaneiroRío de Janeiro	Centro de Encomiendas Santa FeRío de Janeiro 1937	+54 341 431-3044		disponible	\N	2026-04-11 02:24:36.492471	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
65	DAS Mayorista	DISTRIBUIDORAS / MAYORISTAS	+54 341 692-0606		disponible	\N	2026-04-11 02:24:38.802167	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
70	CELTRON Ingeniería InformáticaSan Juan	SERVICIOS TÉCNICOS	+54 341 447-5062		disponible	\N	2026-04-11 02:24:41.22814	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
75	Reparación PC RosarioRioja	SERVICIOS TÉCNICOS	+54 341 616-9789		disponible	\N	2026-04-11 02:24:43.611652	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
56	Roca Cargas	LOGÍSTICA / TRANSPORTE	+54 341 354-3988		disponible	\N	2026-04-11 02:24:34.646009	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
61	LOGÍSTICA TRANSPORTIA SRLZinni	Centro de Encomiendas Santa FeRío de Janeiro 1937	+54 341 422-3210		disponible	\N	2026-04-11 02:24:36.951183	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
66	Imán Mayorista S.R.L.	DISTRIBUIDORAS / MAYORISTAS	+54 9 341 315-7857		disponible	\N	2026-04-11 02:24:39.330944	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
71	TechreviveItalia 273	SERVICIOS TÉCNICOS	+54 341 502-4695		disponible	\N	2026-04-11 02:24:41.694405	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
76	Serv. Técnico Computadoras 24hsBlvd. 27 de Febrero	SERVICIOS TÉCNICOS	+54 9 341 348-5890		disponible	\N	2026-04-11 02:24:44.137786	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
57	FRANSOF S.R.L. Logística y DistribuciónUruguay	LOGÍSTICA / TRANSPORTE	+54 9 341 390-7922		disponible	\N	2026-04-11 02:24:35.110906	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
62	Distributor Wholesaler EduvigesBlvd. 27 de Febrero	DISTRIBUIDORAS / MAYORISTAS	+54 341 432-5446		disponible	\N	2026-04-11 02:24:37.414838	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
67	DR Distribuciones RosarioLamadrid 220	Distribuidora MatienzoMatienzo 2643	+54 341 699-0701		disponible	\N	2026-04-11 02:24:39.789096	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
72	RedPro Servicio TécnicoMarcos Paz	SERVICIOS TÉCNICOS	+54 341 254-3441		disponible	\N	2026-04-11 02:24:42.162267	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
77	Tornería CNC MecaniZarGarzón 768 bis	METALÚRGICAS	+54 341 521-7756		disponible	\N	2026-04-11 02:28:07.694035	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
78	Raposo MecanizadosStephenson Bis 131	METALÚRGICAS	+54 341 552-4731		disponible	\N	2026-04-11 02:28:08.286436	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
79	Metalúrgica CarriegoNueva York 384	METALÚRGICAS	+54 341 437-2283		disponible	\N	2026-04-11 02:28:08.817778	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
80	CNC MecanizadoTucumán	METALÚRGICAS	+54 341 695-3565		disponible	\N	2026-04-11 02:28:09.363602	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
81	Drovet Casa CentralMadres Pl. 25 de	AGRO	+54 341 322-8815		disponible	\N	2026-04-11 02:28:09.82528	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
82	New Integral Field	AGRO	+54 341 432-7227		disponible	\N	2026-04-11 02:28:10.338605	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
83	MARELLI CONSTRUCTORA S.A.Blvd. Oroño 568	️ CONSTRUCCIÓN	+54 341 421-1269		disponible	\N	2026-04-11 02:28:10.799425	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
84	OBRING S.A.	️ CONSTRUCCIÓN	+54 341 598-5585		disponible	\N	2026-04-11 02:28:11.258617	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
85	On Construcciones S.R.L.	️ CONSTRUCCIÓN	+54 9 341 301-8847		disponible	\N	2026-04-11 02:28:11.722483	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
86	Mastrodicasa ConstruccionesDr. Juan B. Justo	Pascual ConstruccionesEntre Ríos 655 P.11	+54 341 836-9225		disponible	\N	2026-04-11 02:28:12.18202	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
87	IZUCO CONSTRUCTORA	Pascual ConstruccionesEntre Ríos 655 P.11	+54 341 451-7300		disponible	\N	2026-04-11 02:28:12.705569	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
88	Del Sol Constructora S.A.	Pascual ConstruccionesEntre Ríos 655 P.11	+54 341 435-8001		disponible	\N	2026-04-11 02:28:13.157332	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
89	Milicic S.A.	Pascual ConstruccionesEntre Ríos 655 P.11	+54 341 409-5600		disponible	\N	2026-04-11 02:28:13.628771	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
90	Grúas E Menchon SRLVera Mujica	TSM INTEGRAL SRLAv. de Las Palmeras 3981	+54 341 439-7007		disponible	\N	2026-04-11 02:28:14.163866	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
91	Grúa y Traslado Servicios MauleJ.S. de Agüero	TSM INTEGRAL SRLAv. de Las Palmeras 3981	+54 9 341 642-6561		disponible	\N	2026-04-11 02:28:14.69268	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
92	Dumar SRLColón	DISTRIBUIDORAS	+54 341 481-8844		disponible	\N	2026-04-11 02:28:15.149169	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
93	Del Parque Distribuidora	DISTRIBUIDORAS	+54 341 541-5459		disponible	\N	2026-04-11 02:28:15.672441	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
94	DUchini MAría - Prod. DUMARRueda 66	DISTRIBUIDORAS	+54 341 485-5864		disponible	\N	2026-04-11 02:28:16.13768	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
95	Distribuidora Oeste	DISTRIBUIDORAS	+54 341 356-9516		disponible	\N	2026-04-11 02:28:16.601279	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
96	Limpiahome9 de Julio	DISTRIBUIDORAS	+54 341 252-3836		disponible	\N	2026-04-11 02:28:17.060528	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
97	Industrias LitoralVera Mujica	DISTRIBUIDORAS	+54 341 432-2424		disponible	\N	2026-04-11 02:28:17.594209	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
98	Bellotti SRLJosé Hernández 739	DISTRIBUIDORAS	+54 341 327-4724		disponible	\N	2026-04-11 02:28:18.118214	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
99	CRISER REPUESTOSRioja	SERVICIOS TÉCNICOS	+54 341 552-2666		disponible	\N	2026-04-11 02:28:18.574464	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
100	CRISER REPUESTOSMendoza	SERVICIOS TÉCNICOS	+54 341 216-0872		disponible	\N	2026-04-11 02:28:19.032176	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
101	Service Italia - RepuestosCorrientes	SERVICIOS TÉCNICOS	+54 341 545-9337		disponible	\N	2026-04-11 02:28:19.491319	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
102	Servicio Técnico Drean/AuroraValentín Gómez	SERVICIOS TÉCNICOS	+54 341 547-3594		disponible	\N	2026-04-11 02:28:20.010221	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
103	Servicio Técnico Rosario9 de Julio 337	SERVICIOS TÉCNICOS	+54 9 341 227-6905		disponible	\N	2026-04-11 02:28:20.476472	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
104	Técnico Refrigeración y Lavarropas EstebanGarcilazo	Service de Lavarropas RosarioConstitución 2281	+54 341 500-2768		disponible	\N	2026-04-11 02:28:20.946792	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
1	Herrería Santa FePedro de Vega 2973, SF	METALÚRGICAS — Nuevas	+54 342 488-5041		disponible	\N	2026-04-11 02:14:56.96592	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
105	CHIAPERO SRL (ruedas industriales)Bolivia Bis 313	ANDES S.A. (lubricantes industriales)Av. Circunvalación 25 de Mayo 1166	+54 341 456-5902		disponible	\N	2026-04-12 02:16:26.708431	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
106	ACEROS ESPECIALES S.R.L.	ANDES S.A. (lubricantes industriales)Av. Circunvalación 25 de Mayo 1166	+54 341 464-4128		disponible	\N	2026-04-12 02:16:27.301186	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
107	Hierros RosarioBlvd. Seguí	ANDES S.A. (lubricantes industriales)Av. Circunvalación 25 de Mayo 1166	+54 341 550-9582		disponible	\N	2026-04-12 02:16:27.845465	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
108	HERRAMAT Steels S.A.Cam. de los Granaderos	ANDES S.A. (lubricantes industriales)Av. Circunvalación 25 de Mayo 1166	+54 341 372-8328		disponible	\N	2026-04-12 02:16:28.331044	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
109	Comeco Steels S.A.	ANDES S.A. (lubricantes industriales)Av. Circunvalación 25 de Mayo 1166	+54 341 446-0900		disponible	\N	2026-04-12 02:16:28.859436	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
110	Aceros Cufer - Rosario	ANDES S.A. (lubricantes industriales)Av. Circunvalación 25 de Mayo 1166	+54 341 522-6100		disponible	\N	2026-04-12 02:16:29.446881	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
111	ACEROS COCOC.	ANDES S.A. (lubricantes industriales)Av. Circunvalación 25 de Mayo 1166	+54 341 409-3200		disponible	\N	2026-04-12 02:16:29.942981	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
112	Industrial Aceros S.R.L.Col. Oeste RP 21	ANDES S.A. (lubricantes industriales)Av. Circunvalación 25 de Mayo 1166	+54 341 317-7788		disponible	\N	2026-04-12 02:16:30.547286	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
113	Hierros Avellaneda	ANDES S.A. (lubricantes industriales)Av. Circunvalación 25 de Mayo 1166	+54 341 604-2411		disponible	\N	2026-04-12 02:16:31.015545	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
114	Sidinox (aceros inoxidables)Virasoro	ANDES S.A. (lubricantes industriales)Av. Circunvalación 25 de Mayo 1166	+54 341 482-7202		disponible	\N	2026-04-12 02:16:31.503865	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
115	Rogiro Aceros SACam. Límite Municipio	ANDES S.A. (lubricantes industriales)Av. Circunvalación 25 de Mayo 1166	+54 341 452-7200		disponible	\N	2026-04-12 02:16:31.96261	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
116	LogimetPérez	ANDES S.A. (lubricantes industriales)Av. Circunvalación 25 de Mayo 1166	+54 341 526-3918		disponible	\N	2026-04-12 02:16:32.790285	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
117	ROVIAL S.A.Leandro N. Alem	️ CONSTRUCCIÓN / VIAL	+54 341 485-4540		disponible	\N	2026-04-12 02:16:33.2541	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
118	Sasa Rosario S.R.L. (asfalto/vial)Ruta Nac. 34 km 4.5	️ CONSTRUCCIÓN / VIAL	+54 341 490-4162		disponible	\N	2026-04-12 02:16:33.725723	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
119	GRUPO RJG	️ CONSTRUCCIÓN / VIAL	+54 341 495-6565		disponible	\N	2026-04-12 02:16:34.264845	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
120	Derosario Servicios S.R.L.	️ CONSTRUCCIÓN / VIAL	+54 341 330-9133		disponible	\N	2026-04-12 02:16:34.742665	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
121	Grupo Silcar (logística/autoelevadores)Juan Pablo II	LOGÍSTICA / DEPÓSITOS	+54 341 465-3737		disponible	\N	2026-04-12 02:16:35.2108	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
122	Inner LogisticsJuan Pablo II	LOGÍSTICA / DEPÓSITOS	+54 341 690-1786		disponible	\N	2026-04-12 02:16:35.669844	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
123	Portar (depósito portuario)José María Rosa	LOGÍSTICA / DEPÓSITOS	+54 341 568-9767		disponible	\N	2026-04-12 02:16:36.141633	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
128	"Irlanda" DistribucionesViamonte	DISTRIBUIDORAS	+54 341 588-2532		disponible	\N	2026-04-12 02:16:38.792499	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
132	L&L Metalúrgica1 de Agosto 405	METALÚRGICAS	+54 3492 24-9741		disponible	\N	2026-04-12 02:17:07.371431	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
137	Rafaela Materiales	️ CONSTRUCCIÓN / CORRALONES	+54 3492 61-1220		disponible	\N	2026-04-12 02:17:09.945634	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
142	Corralón GramagliaM. de Azcuénaga	MT ConstruccionesEmiliano Cerdan 875	+54 3492 42-1021		disponible	\N	2026-04-12 02:17:12.362381	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
147	Transporte AcuñaCdad. de Sunchales 410	LOGÍSTICA / TRANSPORTE	+54 3492 58-5243		disponible	\N	2026-04-12 02:17:14.834782	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
152	Rosso TransporteJ. Zanetti 190	Rey Mar FletHermana Fortunata 535	+54 3492 30-2092		disponible	\N	2026-04-12 02:17:17.228319	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
157	Digital Solutions S.A.C. San Lorenzo 414	SERVICIOS TÉCNICOS	+54 3492 39-4229		disponible	\N	2026-04-12 02:17:19.638826	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
162	Cz Técnica	Electrónica & Informática RafaelaProf. Ariel Abdala 2344	+54 3492 50-2292		disponible	\N	2026-04-12 02:17:22.020288	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
124	Mayorista de Bebidas "Oroño"Blvd. Oroño	DISTRIBUIDORAS	+54 341 675-7452		disponible	\N	2026-04-12 02:16:36.607171	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
129	NutrirJulián de Leyva	DISTRIBUIDORAS	+54 341 767-0146		disponible	\N	2026-04-12 02:16:39.330345	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
133	Taller Wilde (galvanizado/zinc)Gob. Begnis	METALÚRGICAS	+54 9 3492 67-8555		disponible	\N	2026-04-12 02:17:07.88883	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
138	Walter MaterialesConscripto E. Zurbriggen 353	️ CONSTRUCCIÓN / CORRALONES	+54 3492 21-6161		disponible	\N	2026-04-12 02:17:10.40581	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
143	Menara Construcciones	MT ConstruccionesEmiliano Cerdan 875	+54 3492 43-1110		disponible	\N	2026-04-12 02:17:12.832333	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
148	Via Cargo Rafaela	LOGÍSTICA / TRANSPORTE	+54 9 3492 67-2070		disponible	\N	2026-04-12 02:17:15.296516	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
153	Distribuidora Huerpas	DISTRIBUIDORAS	+54 9 3492 69-5277		disponible	\N	2026-04-12 02:17:17.700055	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
158	APC Digital RafaelaJ. Larrea	SERVICIOS TÉCNICOS	+54 3492 21-59082		disponible	\N	2026-04-12 02:17:20.100968	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
163	MEGA ELECTRÓNICA RAFAELARemedios de Escalada 409	Electrónica & Informática RafaelaProf. Ariel Abdala 2344	+54 9 3492 64-2233		disponible	\N	2026-04-12 02:17:22.556547	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
125	Ritual DistribuidoraVélez Sársfield	DISTRIBUIDORAS	+54 341 681-4680		disponible	\N	2026-04-12 02:16:37.117537	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
134	Integral Agropecuaria SAColón 437	AGRO	+54 3492 43-2110		disponible	\N	2026-04-12 02:17:08.491214	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
139	Menara Corralón S.A.	️ CONSTRUCCIÓN / CORRALONES	+54 3492 50-6900		disponible	\N	2026-04-12 02:17:10.88389	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
144	Corralón	MT ConstruccionesEmiliano Cerdan 875	+54 3492 32-1070		disponible	\N	2026-04-12 02:17:13.302537	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
149	Expreso Santa Rosa SA	Rey Mar FletHermana Fortunata 535	+54 810-144-0610		disponible	\N	2026-04-12 02:17:15.757415	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
154	Shuk DistribuidoraLorenzatti	Soffietti Distribuciones SAFrancia 917	+54 9 3492 24-3373		disponible	\N	2026-04-12 02:17:18.2345	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
159	G InformáticaFalucho	SERVICIOS TÉCNICOS	+54 9 3492 68-7251		disponible	\N	2026-04-12 02:17:20.569984	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
126	Aguas R. MartínezDíaz Vélez 122	DISTRIBUIDORAS	+54 341 469-3257		disponible	\N	2026-04-12 02:16:37.704537	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
130	Metalúrgica Rafaela	METALÚRGICAS	+54 3492 44-0275		disponible	\N	2026-04-12 02:17:06.382137	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
135	AGRO FRONTERA SRL	AGRO	+54 9 3564 21-9232		disponible	\N	2026-04-12 02:17:08.952949	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
140	Aconcagua Materiales	️ CONSTRUCCIÓN / CORRALONES	+54 3492 42-9339		disponible	\N	2026-04-12 02:17:11.353739	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
145	Expreso OmegaEmilio Galassi 60	LOGÍSTICA / TRANSPORTE	+54 3492 57-9415		disponible	\N	2026-04-12 02:17:13.835711	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
150	Rapiflet CarolinaConscripto E. Zurbriggen 693	Rey Mar FletHermana Fortunata 535	+54 3492 42-3966		disponible	\N	2026-04-12 02:17:16.232272	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
155	Polar Distribuciones SA	Alimentos AméricaLópez y Planes 1796	+54 3492 45-2200		disponible	\N	2026-04-12 02:17:18.702556	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
160	Electrónica & Servicios	Electrónica & Informática RafaelaProf. Ariel Abdala 2344	+54 3492 43-6635		disponible	\N	2026-04-12 02:17:21.096863	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
127	LIDER Distribuidora	DISTRIBUIDORAS	+54 341 251-6552		disponible	\N	2026-04-12 02:16:38.249532	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
131	AF MetalúrgicaFernando Fader 911	METALÚRGICAS	+54 3492 57-0394		disponible	\N	2026-04-12 02:17:06.838023	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
136	SIA Rafaela (repuestos agrícolas)	Agrícola RafaelaRuta 34 Km 224	+54 3492 68-6269		disponible	\N	2026-04-12 02:17:09.479007	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
141	Corralón Dellasanta S.A.Monteagudo 249	️ CONSTRUCCIÓN / CORRALONES	+54 9 3492 50-7210		disponible	\N	2026-04-12 02:17:11.818507	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
146	Transporte Bailetti San Francisco	LOGÍSTICA / TRANSPORTE	+54 3492 67-5734		disponible	\N	2026-04-12 02:17:14.366365	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
151	Transporte el Provinciano SRLRP70 km 85	Rey Mar FletHermana Fortunata 535	+54 3492 43-2875		disponible	\N	2026-04-12 02:17:16.761173	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
156	Distribuidora Don Angel S.R.L.Santos Vega 431	Alimentos AméricaLópez y Planes 1796	+54 3492 28-4723		disponible	\N	2026-04-12 02:17:19.174469	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
161	HRO Servicio Técnico3 de Febrero 565	Electrónica & Informática RafaelaProf. Ariel Abdala 2344	+54 3492 20-9125		disponible	\N	2026-04-12 02:17:21.558735	\N	\N	f	\N	\N	\N	\N	\N	\N	\N	\N	\N	basico	0	50	\N
\.


--
-- Data for Name: comunidad_comentarios; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.comunidad_comentarios (id, post_id, aliado_id, cuerpo, creado_en) FROM stdin;
\.


--
-- Data for Name: comunidad_posts; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.comunidad_posts (id, aliado_id, tipo, titulo, cuerpo, likes, fijado, oculto, creado_en) FROM stdin;
\.


--
-- Data for Name: prospectos; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.prospectos (id, aliado_id, nombre, contacto, plan_interes, estado, nota, interesante, fecha_contacto, fecha_respuesta, creado_en, rubro, tamano, urgencia, score_ia, plan_recomendado, pitch_sugerido, perfilado_en, automation_paso, automation_ultimo_en, automation_activa_desde) FROM stdin;
1	32	Magna Partes, C.A.	0414-5292083		contactado		f	2026-04-07 01:44:15.02106	\N	2026-04-07 01:44:00.666334	\N	\N	\N	0	\N	\N	\N	0	\N	\N
\.


--
-- Data for Name: referidos; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.referidos (id, aliado_id, nombre_cliente, plan_elegido, notas, registrado_en, acuse_recibo, convertido) FROM stdin;
2	17	Spreafico Equipamentos SRL	Plan Base	El cliente desconoce totalmente de webs y sistemas, le interesa si es que le da resultado, probablemente subir de plan en un futuro y capaz podemos implementarle también mantenimiento mensual. 	2026-04-08 16:16:18.665774	f	f
\.


--
-- Data for Name: transacciones_credito; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.transacciones_credito (id, aliado_id, delta, motivo, referencia, creado_en) FROM stdin;
\.


--
-- Data for Name: ventas; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.ventas (id, aliado_id, referido_id, nombre_cliente, plan, valor_usd, comision_pct, comision_usd, confirmada, pagada, fecha_venta, fecha_pago, modalidad_pago, notas, creado_en, cuotas, financiacion_pct) FROM stdin;
\.


--
-- Name: admins_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.admins_id_seq', 1, true);


--
-- Name: aliados_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.aliados_id_seq', 37, true);


--
-- Name: auditorias_log_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.auditorias_log_id_seq', 2, true);


--
-- Name: automation_log_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.automation_log_id_seq', 1, false);


--
-- Name: bolsa_leads_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.bolsa_leads_id_seq', 163, true);


--
-- Name: comunidad_comentarios_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.comunidad_comentarios_id_seq', 1, false);


--
-- Name: comunidad_posts_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.comunidad_posts_id_seq', 1, false);


--
-- Name: prospectos_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.prospectos_id_seq', 1, true);


--
-- Name: referidos_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.referidos_id_seq', 2, true);


--
-- Name: transacciones_credito_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.transacciones_credito_id_seq', 1, false);


--
-- Name: ventas_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.ventas_id_seq', 1, false);


--
-- Name: admins admins_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.admins
    ADD CONSTRAINT admins_pkey PRIMARY KEY (id);


--
-- Name: admins admins_username_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.admins
    ADD CONSTRAINT admins_username_key UNIQUE (username);


--
-- Name: aliados aliados_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.aliados
    ADD CONSTRAINT aliados_pkey PRIMARY KEY (id);


--
-- Name: aliados aliados_ref_code_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.aliados
    ADD CONSTRAINT aliados_ref_code_key UNIQUE (ref_code);


--
-- Name: auditorias_log auditorias_log_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.auditorias_log
    ADD CONSTRAINT auditorias_log_pkey PRIMARY KEY (id);


--
-- Name: automation_log automation_log_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.automation_log
    ADD CONSTRAINT automation_log_pkey PRIMARY KEY (id);


--
-- Name: bolsa_leads bolsa_leads_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.bolsa_leads
    ADD CONSTRAINT bolsa_leads_pkey PRIMARY KEY (id);


--
-- Name: comunidad_comentarios comunidad_comentarios_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.comunidad_comentarios
    ADD CONSTRAINT comunidad_comentarios_pkey PRIMARY KEY (id);


--
-- Name: comunidad_posts comunidad_posts_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.comunidad_posts
    ADD CONSTRAINT comunidad_posts_pkey PRIMARY KEY (id);


--
-- Name: prospectos prospectos_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.prospectos
    ADD CONSTRAINT prospectos_pkey PRIMARY KEY (id);


--
-- Name: referidos referidos_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.referidos
    ADD CONSTRAINT referidos_pkey PRIMARY KEY (id);


--
-- Name: transacciones_credito transacciones_credito_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.transacciones_credito
    ADD CONSTRAINT transacciones_credito_pkey PRIMARY KEY (id);


--
-- Name: ventas ventas_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ventas
    ADD CONSTRAINT ventas_pkey PRIMARY KEY (id);


--
-- Name: ix_admins_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_admins_id ON public.admins USING btree (id);


--
-- Name: ix_aliados_codigo; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX ix_aliados_codigo ON public.aliados USING btree (codigo);


--
-- Name: ix_aliados_email; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX ix_aliados_email ON public.aliados USING btree (email);


--
-- Name: ix_aliados_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_aliados_id ON public.aliados USING btree (id);


--
-- Name: ix_auditorias_log_dominio; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_auditorias_log_dominio ON public.auditorias_log USING btree (dominio);


--
-- Name: ix_auditorias_log_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_auditorias_log_id ON public.auditorias_log USING btree (id);


--
-- Name: ix_auditorias_log_ref_code; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_auditorias_log_ref_code ON public.auditorias_log USING btree (ref_code);


--
-- Name: ix_automation_log_aliado_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_automation_log_aliado_id ON public.automation_log USING btree (aliado_id);


--
-- Name: ix_automation_log_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_automation_log_id ON public.automation_log USING btree (id);


--
-- Name: ix_automation_log_prospecto_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_automation_log_prospecto_id ON public.automation_log USING btree (prospecto_id);


--
-- Name: ix_bolsa_leads_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_bolsa_leads_id ON public.bolsa_leads USING btree (id);


--
-- Name: ix_comunidad_comentarios_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_comunidad_comentarios_id ON public.comunidad_comentarios USING btree (id);


--
-- Name: ix_comunidad_posts_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_comunidad_posts_id ON public.comunidad_posts USING btree (id);


--
-- Name: ix_prospectos_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_prospectos_id ON public.prospectos USING btree (id);


--
-- Name: ix_referidos_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_referidos_id ON public.referidos USING btree (id);


--
-- Name: ix_transacciones_credito_aliado_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_transacciones_credito_aliado_id ON public.transacciones_credito USING btree (aliado_id);


--
-- Name: ix_transacciones_credito_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_transacciones_credito_id ON public.transacciones_credito USING btree (id);


--
-- Name: ix_ventas_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_ventas_id ON public.ventas USING btree (id);


--
-- Name: aliados aliados_sponsor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.aliados
    ADD CONSTRAINT aliados_sponsor_id_fkey FOREIGN KEY (sponsor_id) REFERENCES public.aliados(id);


--
-- Name: auditorias_log auditorias_log_aliado_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.auditorias_log
    ADD CONSTRAINT auditorias_log_aliado_id_fkey FOREIGN KEY (aliado_id) REFERENCES public.aliados(id);


--
-- Name: automation_log automation_log_aliado_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.automation_log
    ADD CONSTRAINT automation_log_aliado_id_fkey FOREIGN KEY (aliado_id) REFERENCES public.aliados(id);


--
-- Name: automation_log automation_log_prospecto_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.automation_log
    ADD CONSTRAINT automation_log_prospecto_id_fkey FOREIGN KEY (prospecto_id) REFERENCES public.prospectos(id);


--
-- Name: bolsa_leads bolsa_leads_aliado_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.bolsa_leads
    ADD CONSTRAINT bolsa_leads_aliado_id_fkey FOREIGN KEY (aliado_id) REFERENCES public.aliados(id);


--
-- Name: comunidad_comentarios comunidad_comentarios_aliado_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.comunidad_comentarios
    ADD CONSTRAINT comunidad_comentarios_aliado_id_fkey FOREIGN KEY (aliado_id) REFERENCES public.aliados(id);


--
-- Name: comunidad_comentarios comunidad_comentarios_post_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.comunidad_comentarios
    ADD CONSTRAINT comunidad_comentarios_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.comunidad_posts(id);


--
-- Name: comunidad_posts comunidad_posts_aliado_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.comunidad_posts
    ADD CONSTRAINT comunidad_posts_aliado_id_fkey FOREIGN KEY (aliado_id) REFERENCES public.aliados(id);


--
-- Name: prospectos prospectos_aliado_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.prospectos
    ADD CONSTRAINT prospectos_aliado_id_fkey FOREIGN KEY (aliado_id) REFERENCES public.aliados(id);


--
-- Name: referidos referidos_aliado_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.referidos
    ADD CONSTRAINT referidos_aliado_id_fkey FOREIGN KEY (aliado_id) REFERENCES public.aliados(id);


--
-- Name: transacciones_credito transacciones_credito_aliado_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.transacciones_credito
    ADD CONSTRAINT transacciones_credito_aliado_id_fkey FOREIGN KEY (aliado_id) REFERENCES public.aliados(id);


--
-- Name: ventas ventas_aliado_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ventas
    ADD CONSTRAINT ventas_aliado_id_fkey FOREIGN KEY (aliado_id) REFERENCES public.aliados(id);


--
-- Name: ventas ventas_referido_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ventas
    ADD CONSTRAINT ventas_referido_id_fkey FOREIGN KEY (referido_id) REFERENCES public.referidos(id);


--
-- PostgreSQL database dump complete
--

\unrestrict kjDmqapruizoBVvsAbcv0ekQptnliI2R9f0Mov1q8of8yt4ntHVCbR0mNHickPE

