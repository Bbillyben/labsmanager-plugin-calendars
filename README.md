# Calendars V1.7

Calendars est un plugin **externe** qui possède ses modèles, ses API et sa page React. Il ne publie rien dans les calendriers natifs de LabsManager et n'utilise pas `CalendarEventMixin`.

## Données et droits

L'application Django `plugins.Calendars` porte le label stable `plugin_calendars`. Sa migration `0001_initial` crée `Calendar`, `EventType`, `Event` et `CalendarAccess`. Un calendrier n'a aucun rattachement obligatoire à un employé, une équipe ou un projet. Son créateur a toujours le droit `manage` ; les autres accès sont enregistrés par utilisateur dans `CalendarAccess`. `manage` inclut `edit`, qui inclut `view`. Le droit `edit` autorise la création, la modification **et la suppression de tout événement du calendrier**. Seul `manage` permet de changer les paramètres, les types et les accès. Un calendrier invisible retourne 404 ; une opération supérieure au droit détenu retourne 403. Tous les endpoints vérifient ces droits côté Django.

La migration `0002_calendar_access_mode_calendar_employees_and_more` ajoute trois relations N:N natives vers `staff.Employee`, `staff.Team` et `project.Project`, ainsi que `access_mode`. Les calendriers existants restent `restricted` par défaut. Dans ce mode, seuls le créateur, un superutilisateur Django et les utilisateurs de `CalendarAccess` ont accès. En mode `inherited`, consulter **au moins un** objet associé donne `view`, sans jamais donner `edit` ni `manage`. Les listes visibles d'Employee, Team et Project du cœur sont réutilisées ; les droits explicites peuvent élever le niveau effectif. Un calendrier `inherited` sans rattachement suit la capacité du calendrier général React (`common.display_calendar` ou `leave.view_leave`) ; ce droit ne s'applique pas à un calendrier portant des rattachements invisibles. Le superutilisateur Django possède `manage` sur tous les calendriers, indépendamment du mode. Il ne suffit pas d'être `is_staff`, responsable d'équipe, chef de projet ou membre de `Lab_Manager`.

Seul `manage` peut lire ou modifier les rattachements. Un nouvel objet associé doit être visible selon les requêtes métier du cœur ; les objets déjà associés peuvent être conservés ou retirés par un gestionnaire même si sa visibilité sur l'objet a changé. Le `PATCH` des rattachements remplace atomiquement les listes fournies et laisse les autres intactes. Les relations N:N interdisent les doublons et suppriment leurs lignes lors de la suppression d'un objet associé.

Les événements horaires sont stockés en dates/horaires avec fuseau. Les événements sur journée entière utilisent des dates locales de début et de **fin exclusive** : `2026-03-12` → `2026-03-13` représente le 12 mars entier, conformément à FullCalendar. La migration n'est jamais exécutée par le plugin.

La migration `0003_calendar_type_event_employees` ajoute `calendar_type` (`events` par défaut pour les calendriers existants, ou `assignments`) et la relation N:N facultative `Event.employees`. Le type est indépendant de `access_mode`, des rattachements et des `EventType`. Il peut changer tant que le calendrier est vide ; dès le premier événement, il est verrouillé. Un calendrier `events` interdit toute affectation, également en écriture directe par la relation Django. Un événement `assignments` accepte zéro à plusieurs employés, sans doublons ; retirer le dernier ne supprime pas l'événement.

Les employés éligibles sont l'union des employés directement rattachés, des participants **actuels** des projets, des membres **actuels** des équipes et de leurs responsables. Sans rattachement, tous les employés visibles et actifs sont éligibles. Dans tous les cas, la visibilité Employee du cœur est appliquée avant de renvoyer un résultat. Le sélecteur interroge le serveur par recherche paginée (10 résultats) et n'extrait pas tout le personnel. Les affectations historiques restent enregistrées après changement de rattachement, de participation ou de composition d'équipe. Une modification d'événement peut les conserver ou les retirer ; tout nouvel employé est validé selon l'éligibilité actuelle. Les affectations devenues invisibles pour l'utilisateur ne sont ni divulguées ni retirées par son formulaire. L'affectation ne donne aucun droit sur le calendrier.

## API

Routes sous `/plugin/calendars/api/calendars/`, disponibles lorsque le plugin et `UrlsMixin` sont actifs :

| Route | Méthodes | Droit |
| --- | --- | --- |
| `/` | GET liste visible, POST création | Session authentifiée |
| `/<id>/` | GET, PATCH | view, manage |
| `/<id>/attachments/` | GET, PATCH listes `employees`, `teams`, `projects` | manage |
| `/<id>/eligible-employees/?q=...` ou `?id=...` | GET recherche des employés sélectionnables | view |
| `/<id>/resources/?start=...&end=...` | GET lignes Employee éligibles et visibles | view |
| `/<id>/users/?q=...` | GET recherche de comptes actifs | manage |
| `/<id>/members/` | GET, POST ajout ou mise à jour | manage |
| `/<id>/members/<user_id>/` | PATCH, DELETE | manage |
| `/<id>/types/` | GET, POST | view, manage |
| `/<id>/types/<type_id>/` | PATCH, DELETE | manage |
| `/<id>/events/?start=...&end=...` | GET chevauchement | view |
| `/<id>/events/` | POST | edit |
| `/<id>/events/<event_id>/` | GET, PATCH, DELETE | view, edit |

La liste accepte `?scope=general` pour les calendriers sans rattachement Employee, Team ou Project et `?scope=managed` pour les calendriers administrables par l'utilisateur (tous les calendriers pour un superutilisateur). La réponse `managed` inclut les trois listes de rattachements pour le tableau d'administration. Sans `scope`, la liste conserve sa réponse historique de calendriers visibles. Les filtres contextuels `employee_id`, `team_id` et `project_id` restent distincts.

Les réponses calendrier exposent `calendar_type` et `has_events`. Les réponses événement exposent `event_type_name` et `employees` sous forme de `{id, label}` pour les employés consultables. À l'écriture d'un événement `assignments`, `employees` est une liste d'identifiants, facultative et éventuellement vide. Sur `PATCH`, l'absence de la clé laisse les affectations intactes ; sa présence remplace les affectations visibles, en conservant les éventuelles affectations invisibles préexistantes. Le détail et les vues FullCalendar affichent les affectations sans dupliquer les événements ; une indication compacte du nombre d'affectés accompagne le titre lorsque ce nombre est positif.

Le GET des événements exige une période positive de 370 jours au plus et sélectionne `start < end demandé` et `end > start demandé`. Les types inactifs restent visibles sur les événements existants mais ne peuvent plus être choisis pour un nouveau. Un type utilisé ne peut pas être supprimé ; le désactiver. La recherche d'utilisateurs porte sur les **comptes** ; elle n'infère jamais un compte depuis un employé. La réponse des calendriers inclut `access_mode` et les capacités `can_edit`/`can_manage`. La réponse des événements calcule leur couleur avec priorité à la couleur du type, puis à celle du calendrier ; FullCalendar 7 utilise la propriété `color`.

## Interface et compilation indépendante

Les textes propres au plugin sont dans `frontend/src/locales/fr.json` et `en.json`. `main.tsx` les importe dans le bundle différé, les enregistre sous le slug `calendars` avec `registerPluginTranslations`, puis les lit via `usePluginTranslation('calendars')` dans la page générale, les onglets contextuels Employee/Team/Project et les paramètres. La langue suit le contexte React du cœur, y compris si elle change pendant l'affichage. Une clé manquante se replie sur le français, puis l'anglais ; une langue supplémentaire exige d'abord son ajout au contrat du cœur et un nouveau dictionnaire dans le plugin. Le contrat et un exemple complet figurent dans `docs/react-migration/PLUGIN_UI.md` du cœur. Après modification des JSON, reconstruire le frontend du cœur si son API partagée a changé, puis lancer `npm run build` dans `/home/labsmanager/data/plugins/Calendars/frontend/`, et publier `frontend/dist/` via le processus statique habituel.

La page Calendars apparaît également après « Calendrier général » et « Calendrier des projets » dans la navigation des calendriers. Son entrée provient des métadonnées facultatives de sa déclaration `plugin.page` ; sa route et son accès depuis « Mes plugins » sont conservés.

`UIContributionMixin` déclare la page `/app/plugins/calendars/calendars`, un panneau de paramètres sous « Mes plugins → Calendars » et un onglet contextuel commun sur les fiches Employee, Team et Project. La page générale ne présente que les événements des calendriers sans rattachement ; la création et l'administration des calendriers se font dans le tableau du panneau de paramètres. Le menu de ligne ouvre les formulaires existants pour les paramètres, les types, les rattachements et les accès. Aucune suppression de calendrier n'est proposée, car l'API actuelle ne l'autorise pas. L'onglet contextuel est annoncé seulement si le plugin est actif et si au moins un calendrier associé est visible. Les routes directes suivent `/app/<employees|teams|projects>/<id>/plugins/calendars/<entity>-calendars`. Le détail de contribution transmet `{entity, objectId}` au même composant `ContextTab` ; son bundle n'est chargé qu'à l'ouverture de l'onglet et les événements seulement pour la période affichée. Les filtres `?employee_id=`, `?team_id=` et `?project_id=` de la liste vérifient à la fois le droit `view` sur l'objet et l'accès à chaque calendrier. Les calendriers sont tous affichés par défaut et peuvent être masqués individuellement ; couleur et nom du calendrier accompagnent chaque événement. La création d'événement demande un choix explicite parmi les calendriers éditables.

Le module utilise l'instance React, `EntitySearch`, `EntityActionMenu`, les panneaux, boutons, alertes et `PluginCalendarView` du cœur via `@labsmanager/plugin-ui`. Les sources de recherche Employee, Team et Project restent exposées par le même contrat. Le cœur fournit FullCalendar et ses styles ; le **JS/CSS propre au plugin** n'est chargé qu'à l'ouverture de sa page ou d'un onglet.

Depuis `frontend/` avec Node 24 :

```bash
npm install
npm run build
```

Distribuer le package Python, `migrations/`, `frontend/dist/manifest.json` et **tous** les fichiers de `frontend/dist/assets/`. Ni `node_modules` ni les sources TypeScript ne sont nécessaires au serveur. Le contrat P2 et `@labsmanager/plugin-calendar` doivent être présents dans le cœur déployé ; ensuite installer ou mettre à jour Calendars ne demande ni reconstruction du cœur ni reconstruction de son image.

## Installation et mise à jour

1. Copier `Calendars/` dans le répertoire externe configuré par `LABSMANAGER_PLUGIN_DIR` (en Docker, `<LAB_EXT_VOLUME>/plugins/Calendars/`). Ne pas le copier dans `builtin` ou `samples`. Le répertoire parent `plugins/` est un package Python.
2. Avec la configuration et les volumes de la cible, vérifier `python manage.py makemigrations --check --dry-run`, `python manage.py showmigrations plugin_calendars` et `python manage.py migrate --plan` ; appliquer ensuite `python manage.py migrate plugin_calendars --noinput`. En Docker, utiliser le conteneur ponctuel décrit dans `docs/react-migration/PLUGIN_MODELS.md`, avec `--entrypoint python3`, car le contrôle `migrate --check` du démarrage peut refuser de démarrer avant cette étape.
3. Exécuter `python manage.py collectstatic --noinput` avec les fichiers externes présents, puis redémarrer serveur et worker pour que Django découvre l'application et le manifeste. Activer Calendars dans l'administration des plugins ; vérifier que les URLs des plugins sont autorisées par la configuration existante.
4. Ouvrir « Mes plugins » puis « Calendriers ». En bare metal avec Vite, `/plugin/` et `/static/plugins/` sont relayés vers Django. En production nginx sert les fichiers collectés sous `/static/plugins/calendars/`.

Pour mettre à jour l'instance bare metal, travailler et compiler directement dans `/home/labsmanager/data/plugins/Calendars/frontend/` avec Node 24 et `npm run build`. Le code et le bundle actifs se trouvent dans ce répertoire ; ne pas écraser les migrations précédentes ni les données. Pour V1.8, le cœur React doit également être reconstruit car il exporte l'utilitaire de copie partagé. Vérifier l'état de la migration `0003` avant de servir les calendriers d'affectations.

Depuis `/home/django_project/labsmanager/`, avec les paramètres de la cible :

```bash
export LABSMANAGER_PLUGINS_ENABLED=true
export LABSMANAGER_PLUGIN_DIR=/home/labsmanager/data/plugins
python3 backend/manage.py makemigrations plugin_calendars --check --dry-run
python3 backend/manage.py showmigrations plugin_calendars
python3 backend/manage.py migrate plugin_calendars --plan
# À exécuter uniquement lors de l'étape de migration autorisée, avant la mise en service V1.7 :
python3 backend/manage.py migrate plugin_calendars --noinput
python3 backend/manage.py collectstatic --noinput
```

Redémarrer ensuite Django et le worker. En Docker, utiliser la commande de conteneur ponctuel documentée dans `docs/react-migration/PLUGIN_MODELS.md`. L'implantation de cette évolution ne lance pas la migration réelle ; les endpoints Calendars ne doivent pas être utilisés avec le nouveau code avant que `0003` soit appliquée. Vérifier le plan avec `python3 backend/manage.py migrate plugin_calendars --plan` et ne lancer `collectstatic` qu'au déploiement.

Pour une mise à jour de modèle, distribuer sa nouvelle migration et l'appliquer explicitement avant de redémarrer les processus. Ne jamais renommer `plugins.Calendars`, `plugin_calendars` ou `calendars` après déploiement. Retirer le code ne supprime aucune table ni donnée.

La V1 exclut la récurrence, les ressources et contrôles de conflits, l'export ICS, les notifications et les invitations par email.

## Publication et abonnement ICS (V1.8)

Calendars implémente le contrat `PublishedCalendarMixin` du cœur. Les calendriers visibles sont publiés sous l'identité `(calendars, id)` ; chaque demande de métadonnées ou d'événements revérifie le droit `view` du titulaire. Les événements sont filtrés sur l'intervalle `[start, end)` et convertis en `LabsManagerCalendarEvent`. Pour les calendriers d'affectations, seuls les noms des employés visibles pour le titulaire sont transmis au consommateur.

La page générale et les onglets contextuels Employee, Team et Project affichent les actions du slot générique `published-calendar.actions`. L'action « S'abonner » et sa fenêtre sont fournies par ICalExport ; Calendars ne connaît ni son module React ni ses routes. Ouvrir la fenêtre ne crée aucun token. Désactiver ICalExport retire l'action sans affecter les calendriers.

Les calendriers `assignments` annoncent l'option `assigned_to_me`, absente des calendriers `events`. Lorsque le propriétaire du token la demande, le filtre `Event.employees` est appliqué au queryset avant sérialisation ; un utilisateur sans Employee associé obtient un flux vide. Le titre des événements d'affectation assemble le type, le titre et les noms visibles des employés affectés, triés de manière stable, sans modifier le titre stocké ni multiplier les VEVENT. Les métadonnées `links` contiennent seulement les chemins des rattachements Employee, Team et Project consultables ; un calendrier sans rattachement pointe vers la page générale. ICalExport convertit ces chemins en liens absolus et les ajoute à la description ICS.

Cette version n'ajoute aucune migration. Vérifier que la migration précédente `0003` est appliquée avant l'usage des calendriers de type affectations. Compiler le frontend du plugin, d'ICalExport et du cœur (action React partagée), collecter les fichiers statiques, puis redémarrer les processus Django pour actualiser le registre.

## Vues et impression partagées (V1.8)

`Page` et `ContextTab` confient leur affichage à `PluginCalendarView` du cœur. Celui-ci réutilise les périodes `fifteenDays`, `month`, `twoMonths`, `year` et leurs définitions natives. Les modes `calendar` et `list` sont toujours disponibles ; `resources` est proposé lorsqu'au moins un calendrier `assignments` visible est sélectionné. Changer de mode conserve la date et les calendriers cochés. FullCalendar List affiche la date, l'heure, le type, le calendrier d'origine et les employés visibles. Les couleurs EventType et calendrier restent celles de la réponse API. Le bouton « Ajouter un événement » garde la création accessible lorsque la vue liste ne permet pas de sélectionner une case de calendrier ; il ouvre le formulaire existant et reste réservé aux calendriers éditables.

La vue ressources utilise une seule ligne par Employee, y compris en cas de plusieurs calendriers, et place un événement multi-affecté sur toutes ses lignes sans le dupliquer en base. Les événements sans ligne Employee visible, notamment ceux d'un calendrier `events` affiché en même temps, restent dans l'unique ligne virtuelle « Sans ligne employé » ; celle-ci ne crée aucun Employee. `GET /resources/` réutilise l'éligibilité et la visibilité Employee existantes. Pour un calendrier sans rattachement, il borne les lignes aux employés affectés pendant la période demandée, afin d'éviter de charger tout le personnel. Les employés historiquement affectés et toujours visibles sont aussi pris dans la réponse des événements. Les formulaires et permissions d'édition restent ceux du plugin.

Le bouton Imprimer utilise `PrintProvider` du cœur et son renderer `plugin-calendar`. La prévisualisation reçoit uniquement les événements, filtres et lignes déjà affichés ; elle ne déclenche aucun chargement supplémentaire. Les contrôles interactifs sont masqués par le mécanisme d'impression du cœur. Une vérification visuelle reste nécessaire pour les périodes longues, les événements couvrant plusieurs jours et la pagination papier. Aucun nouveau modèle, mixin ou migration n'est requis. Publier le build React du cœur avant le bundle Calendars, exécuter `collectstatic`, puis redémarrer Django pour découvrir les assets du plugin.
