#!/bin/bash
# infobar: da de alta un servidor. Se ejecuta en el Terminal de TU MAC (no dentro del servidor):
#
#   infobar/remoto/alta-servidor.sh [-i ~/.ssh/tu_clave] [--nombre produccion] victor@203.0.113.10 [puerto]
#
# 1. Crea (solo la primera vez) la clave dedicada ~/.ssh/infobar_ed25519, sin passphrase: la usa la
#    app al arrancar sola, y en el servidor solo puede ejecutar el script de lectura.
# 2. Entra UNA vez como tu usuario (una conexión maestra de ssh: la contraseña, si hace falta, se
#    pide solo ahi) y por esa misma conexión crea la carpeta, copia los scripts y ejecuta
#    instalar-en-servidor.sh con sudo (leelo antes). sudo pide su contraseña como mucho una vez.
# 3. Si entraste con contraseña, te ofrece autorizar tu clave personal (nunca la de infobar).
# 4. Prueba la conexión como infobar y da de alta el servidor en la app con la misma regla que
#    Ajustes (repetirlo no lo duplica; un nombre que ya usa otro servidor se rechaza).
#
# -i RUTA      clave con la que entras como administrador (solo esa: IdentitiesOnly)
# --nombre N   nombre en la app (por defecto, el host)
set -euo pipefail
AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$(cd "$AQUI/../.." && pwd)"

dentro_del_servidor() {
  echo "Este script se ejecuta en el Terminal de tu Mac, no dentro del servidor."
  echo "Sal del servidor (exit) y lánzalo en tu Mac con: ~/.cost-guard/infobar/remoto/alta-servidor.sh usuario@host [puerto]"
  echo "(en Ajustes -> Servidores tienes la línea exacta para copiar)."
  exit 1
}
sin_la_app() {
  echo "Este script no tiene la app CostGuard al lado (parece una copia suelta) y la necesita para dar de alta el servidor."
  echo "Lánzalo desde la carpeta de la app: ~/.cost-guard/infobar/remoto/alta-servidor.sh usuario@host [puerto]"
  echo "(en Ajustes -> Servidores tienes la línea exacta para copiar)."
  exit 1
}
# en Linux, por SSH o sin la app al lado y sin instalación de CostGuard: casi seguro que es el propio servidor (sesión ssh o consola web)
TIENE_APP=0; [ -f "$APP/infobar/servidores_alta.py" ] && TIENE_APP=1
if [ "$(uname -s)" = Linux ] && [ ! -d "$HOME/.cost-guard" ] && { [ -n "${SSH_CONNECTION:-}" ] || [ "$TIENE_APP" = 0 ]; }; then dentro_del_servidor; fi
# junto al script tiene que estar la app (para darlo de alta)
[ "$TIENE_APP" = 1 ] || sin_la_app

uso() { echo "uso: $0 [-i ruta_de_tu_clave] [--nombre NOMBRE] usuario@host [puerto]"; exit 1; }
CLAVE_ADMIN=""; NOMBRE=""; DESTINO=""; PUERTO=""
while [ $# -gt 0 ]; do
  case "$1" in
    -i) [ $# -ge 2 ] || uso; CLAVE_ADMIN="$2"; shift 2 ;;
    --nombre) [ $# -ge 2 ] || uso; NOMBRE="$2"; shift 2 ;;
    -*) uso ;;
    *) if [ -z "$DESTINO" ]; then DESTINO="$1"; elif [ -z "$PUERTO" ]; then PUERTO="$1"; else uso; fi; shift ;;
  esac
done
PUERTO="${PUERTO:-22}"
[ -n "$DESTINO" ] || uso
# las mismas reglas que la app (_USUARIO y _HOST de servidores_alta.py)
[[ "$DESTINO" =~ ^[A-Za-z_][A-Za-z0-9._-]{0,31}@[A-Za-z0-9][A-Za-z0-9.-]{0,252}$ ]] || {
  echo "«${DESTINO}» no es un usuario@host válido (host: letras, números, puntos y guiones; sin espacios ni _)."; exit 1; }
[[ "$PUERTO" =~ ^[0-9]{1,5}$ ]] && [ "$((10#$PUERTO))" -gt 0 ] && [ "$((10#$PUERTO))" -lt 65536 ] || { echo "puerto no valido"; exit 1; }
PUERTO="$((10#$PUERTO))"
USUARIO="${DESTINO%%@*}"; HOST="${DESTINO#*@}"
NOMBRE="${NOMBRE:-$HOST}"
CLAVE="$HOME/.ssh/infobar_ed25519"

OPC_CLAVE=()
if [ -n "$CLAVE_ADMIN" ]; then
  case "$CLAVE_ADMIN" in *.pub) echo "Con -i va tu clave PRIVADA (la ruta sin .pub)."; exit 1 ;; esac
  [ "$(basename "$CLAVE_ADMIN")" != infobar_ed25519 ] || {
    echo "~/.ssh/infobar_ed25519 es la clave de solo lectura de infobar: con -i va TU clave de administrador."; exit 1; }
  [ -f "$CLAVE_ADMIN" ] && [ -r "$CLAVE_ADMIN" ] || { echo "No encuentro la clave $CLAVE_ADMIN (o no se puede leer)."; exit 1; }
  OPC_CLAVE=(-i "$CLAVE_ADMIN" -o IdentitiesOnly=yes)
fi

# --- registro en la app: el mismo codigo que usa Ajustes, con el Python de la instalación
PY="$HOME/.cost-guard/venv/bin/python"
[ -x "$PY" ] || PY="$HOME/.cost-guard/venv/Scripts/python.exe"      # instalar_windows.py crea el venv asi, no en venv/bin
[ -x "$PY" ] || PY="$(command -v python3 || true)"
registro() {   # registrar [--comprobar|--probar] nombre host puerto -> 0 hecho/se puede, 4 ya estaba, 2 nombre no válido,
               # 3 nombre de otro servidor, 5 la app no lo lee como infobar, 6 host o puerto no válidos
  [ -n "$PY" ] || return 127
  # sin PYTHONPATH="$APP": "python -m" ya añade el directorio actual al sys.path solo con el cd de
  # abajo: forzarlo hacia a mano rompia si $PY es un python nativo de Windows (no MSYS2) y $APP viene
  # en forma POSIX (/c/Users/...) - ese formato no es una ruta valida para el import de un python nativo
  (cd "$APP" && "$PY" -m infobar.servidores_alta registrar "$@" >/dev/null 2>&1)
}
linea_registro() { printf 'cd %q && %q -m infobar.servidores_alta registrar %q %q %q\n' "$APP" "${PY:-python3}" "$1" "$HOST" "$PUERTO"; }
SIN_REGISTRO=0
RC=0; registro --comprobar "$NOMBRE" "$HOST" "$PUERTO" || RC=$?
case "$RC" in
  0) ;;
  3) echo "Ya hay otro servidor llamado «${NOMBRE}» en la app. Elige otro nombre con --nombre OTRO."; exit 1 ;;
  2) echo "El nombre «${NOMBRE}» no vale (hasta 40 letras, números, espacios, puntos, guiones o _). Elige otro con --nombre."; exit 1 ;;
  6) echo "La app no acepta el host «${HOST}» o el puerto $PUERTO (host: letras, números, puntos y guiones)."; exit 1 ;;
  *) echo "Aviso: no pude consultar la lista de servidores de la app (Python: ${PY:-no encontrado}). Instalo igual y al final te digo cómo añadirlo."
     SIN_REGISTRO=1 ;;
esac

# --- claves publicas: valida «tipo base64 [comentario]» (el comentario solo si es de caracteres seguros)
pub_limpia() {
  local l t b c
  [ -f "$1" ] || return 1
  l="$(head -n 1 "$1" | tr -d '\r')"
  read -r t b c <<<"$l" || true
  case "$t" in ssh-ed25519|ecdsa-sha2-nistp256|ssh-rsa) ;; *) return 1 ;; esac
  [[ "$b" =~ ^[A-Za-z0-9+/]+=*$ ]] && [ "${#b}" -ge 60 ] && [ "${#b}" -le 1100 ] || return 1
  if [[ "${c:-}" =~ ^[A-Za-z0-9@._:+=,-]{1,100}$ ]]; then echo "$t $b $c"; else echo "$t $b"; fi
}
linea_autorizar() {   # la misma línea que da Ajustes: añade la clave a authorized_keys sin duplicarla
  echo "mkdir -p ~/.ssh && chmod 700 ~/.ssh && (grep -qxF '$1' ~/.ssh/authorized_keys 2>/dev/null || echo '$1' >> ~/.ssh/authorized_keys) && chmod 600 ~/.ssh/authorized_keys"
}
# tu clave personal: la .pub de la que pasaste con -i o la primera de las de siempre. NUNCA la de infobar.
PERSONAL=""; PERSONAL_PUB=""
if [ -n "$CLAVE_ADMIN" ]; then
  [ -f "$CLAVE_ADMIN.pub" ] && PERSONAL_PUB="$CLAVE_ADMIN.pub"
else
  for k in id_ed25519 id_ecdsa id_rsa; do
    if [ -f "$HOME/.ssh/$k.pub" ]; then PERSONAL_PUB="$HOME/.ssh/$k.pub"; break; fi
  done
fi
[ -z "$PERSONAL_PUB" ] || PERSONAL="$(pub_limpia "$PERSONAL_PUB" || true)"
if [ -n "$PERSONAL" ] && [ -f "$CLAVE.pub" ] && [ "$(cut -d' ' -f2 <<<"$PERSONAL")" = "$(cut -d' ' -f2 "$CLAVE.pub")" ]; then
  PERSONAL=""                                     # es la de infobar: no se autoriza para un administrador
fi
CREAR_CLAVE="Crea tu clave personal en el Mac con: ssh-keygen -t ed25519   (Intro a todo; la frase es opcional)"

if [ ! -f "$CLAVE" ]; then
  echo "-> creando la clave dedicada $CLAVE"
  mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
  # hostname -s (forma corta) es de BSD/macOS; el hostname de GNU/MSYS2 no tiene opciones. El "cut"
  # da el mismo resultado en los tres sistemas (no hace nada si no hay dominio que recortar).
  ssh-keygen -q -t ed25519 -N "" -C "infobar@$(hostname | cut -d. -f1)" -f "$CLAVE"
fi
PUB="$(cat "$CLAVE.pub")"
[[ "$PUB" =~ ^ssh-ed25519\ [A-Za-z0-9+/=]{60,80}(\ [A-Za-z0-9@._-]{1,60})?$ ]] || {
  echo "La clave $CLAVE.pub no tiene el formato esperado (ssh-ed25519). Borra $CLAVE y $CLAVE.pub y repite."; exit 1; }

# --- una sola autenticación: conexión maestra en un directorio privado; todo lo demás va por ella
CTL="$(mktemp -d /tmp/cg-alta.XXXXXX)"
chmod 700 "$CTL"
SOCK="$CTL/s"
REMOTO=""
# la clave elegida va también aquí: si la maestra muriera, ssh no debe entrar por su cuenta con otras claves (además se comprueba antes de cada paso)
OPC_MAESTRA=(-o "ControlPath=$SOCK" -o ControlMaster=no -p "$PUERTO" ${OPC_CLAVE[@]+"${OPC_CLAVE[@]}"})
admin() { ssh -T "${OPC_MAESTRA[@]}" -- "$DESTINO" "$@" </dev/null; }
exige_maestra() {
  ssh -o "ControlPath=$SOCK" -O check -- "$DESTINO" >/dev/null 2>&1 || {
    echo "Se perdió la conexión con el servidor (la conexión abierta al principio ya no está). Vuelve a lanzar el script: repetirlo no duplica nada."
    exit 1; }
}
cierra() {
  if [ -e "$SOCK" ]; then
    # si la instalación no llegó a borrar su carpeta temporal, se borra aquí (solo una ruta de mktemp exacta)
    if [[ "$REMOTO" =~ ^/tmp/infobar-alta\.[A-Za-z0-9]{6}$ ]]; then admin "rm -rf $REMOTO" >/dev/null 2>&1 || true; fi
    ssh -o "ControlPath=$SOCK" -O exit -- "$DESTINO" >/dev/null 2>&1 || true
  fi
  rm -rf "$CTL"
}
trap cierra EXIT

metodos_rechazados() { grep -io 'permission denied ([^)]*)' "$1" | tail -n 1 | tr '[:upper:]' '[:lower:]' || true; }
explica_conexion() {
  local err="$1" e m
  e="$(tr '[:upper:]' '[:lower:]' <"$err")"
  m="$(metodos_rechazados "$err")"
  echo ""
  case "$e" in
    *"remote host identification has changed"*)
      echo "La huella del servidor ha CAMBIADO respecto a la que tienes guardada. No sigas: puede ser una reinstalación"
      echo "legítima o un ataque. Comprueba la huella en el propio servidor y, si es la buena, quita la vieja con:"
      echo "  ssh-keygen -R $( [ "$PUERTO" = 22 ] && echo "$HOST" || echo "'[$HOST]:$PUERTO'")" ;;
    *"host key verification failed"*)
      echo "No se aceptó la huella del servidor (contestaste que no o no había terminal para preguntarte)."
      echo "Repite en el Terminal y compara la huella con la del servidor antes de aceptarla." ;;
    *"too many authentication failures"*)
      echo "El servidor cortó tras probar demasiadas claves (tu agente ssh tiene muchas)."
      echo "Elige la buena con -i: $0 -i ~/.ssh/tu_clave $DESTINO $PUERTO" ;;
    *"permission denied"*)
      if [ -n "$m" ] && [[ "$m" != *password* ]] && [[ "$m" != *keyboard-interactive* ]]; then
        echo "El servidor solo acepta claves SSH y la tuya no está autorizada para $USUARIO."
        if [ -n "$PERSONAL" ]; then
          echo "Autorízala ejecutando esta línea DENTRO del servidor (consola web de tu proveedor o una sesión"
          echo "que ya tengas abierta), como $USUARIO, y vuelve a lanzar este script:"
          echo ""
          echo "  $(linea_autorizar "$PERSONAL")"
        else
          echo "$CREAR_CLAVE"
          echo "y autorízala en el servidor (Ajustes -> Servidores te da la línea para la consola de tu proveedor)."
        fi
        [ -n "$CLAVE_ADMIN" ] || echo "Si entras con otra clave (p. ej. una .pem), pásala con -i."
      elif [ "$USUARIO" = root ]; then
        echo "Contraseña incorrecta, o el servidor no deja entrar a root con contraseña (PermitRootLogin prohibit-password)."
        echo "Prueba con tu usuario normal (el que usa sudo) o autoriza tu clave para root."
      else
        echo "Contraseña incorrecta para $USUARIO (o ese usuario no puede entrar por SSH)."
      fi ;;
    *"connection refused"*)
      echo "El servidor rechazó la conexión en el puerto $PUERTO: ssh no escucha ahí o un cortafuegos la corta."
      echo "Revisa el puerto (el tercer argumento) y el cortafuegos del proveedor." ;;
    *"timed out"*)
      echo "Tiempo agotado: el host no contesta en el puerto $PUERTO. Revisa la IP, el puerto y el cortafuegos del proveedor." ;;
    *"could not resolve"*|*"name or service not known"*|*"nodename nor servname"*)
      echo "No encuentro el host «${HOST}»: revisa que esté bien escrito (o usa su IP)." ;;
    *"no route to host"*|*"network is unreachable"*)
      echo "No hay ruta hasta $HOST: revisa tu conexión a Internet, la VPN o la IP." ;;
    *"connection closed"*|*"connection reset"*|*"kex_exchange_identification"*)
      echo "El servidor cortó la conexión antes de entrar. Si fallaste varias veces, fail2ban puede haber bloqueado tu IP"
      echo "un rato: espera unos minutos o desbloquéala desde la consola de tu proveedor." ;;
    *"load key"*|*"identity file"*)
      echo "No se pudo usar la clave ${CLAVE_ADMIN:-por defecto} (¿frase incorrecta o formato que ssh no entiende?)." ;;
    *)
      echo "No se pudo entrar como $DESTINO. Lo último que dijo ssh:"
      grep -v -e '^Authenticated to' -e '^Warning: Permanently added' "$err" | tail -n 2 | sed 's/^/  /' || true ;;
  esac
}

echo "-> entrando como $DESTINO (si te pide contraseña, es la de ese usuario en el servidor; solo esta vez)"
# LogLevel=VERBOSE: ssh dice en su salida de errores con que método entro («Authenticated to ... using "password"»)
if ! ssh -M -f -N -o "ControlPath=$SOCK" -o ControlPersist=30m -o ConnectTimeout=10 -o LogLevel=VERBOSE \
     -p "$PUERTO" ${OPC_CLAVE[@]+"${OPC_CLAVE[@]}"} -- "$DESTINO" 2>"$CTL/err"; then
  explica_conexion "$CTL/err"
  exit 1
fi
METODO="$(sed -n 's/^Authenticated to .* using "\([a-z-]*\)".*/\1/p' "$CTL/err" | tail -n 1)"

# --- si entraste con contraseña, ofrece autorizar tu clave personal en el usuario administrador
autoriza_clave() {
  [ "$METODO" != publickey ] || return 0
  if [ -z "$PERSONAL" ]; then
    echo "Consejo: así te pedirá la contraseña cada vez. $CREAR_CLAVE"
    echo "y vuelve a lanzar este script: te ofrecerá autorizarla."
    return 0
  fi
  local tb; tb="$(cut -d' ' -f1-2 <<<"$PERSONAL")"
  exige_maestra
  if admin "grep -qF '$tb' ~/.ssh/authorized_keys 2>/dev/null" >/dev/null 2>&1; then return 0; fi    # ya estaba autorizada
  [ -t 0 ] || return 0
  local r=""
  read -r -p "¿Autorizo tu clave en el servidor para no volver a pedirte la contraseña? [s/N] " r </dev/tty || r=""
  case "$r" in s|S|si|Si|SI|sí|Sí|y|Y) ;; *) return 0 ;; esac
  exige_maestra                                   # el dueño pudo tardar en contestar
  if admin "umask 077; $(linea_autorizar "$PERSONAL")" >/dev/null 2>&1; then
    echo "-> tu clave ($(basename "$PERSONAL_PUB")) queda autorizada para $USUARIO"
  else
    echo "Aviso: no se pudo autorizar tu clave (sigo con el alta)."
  fi
}
autoriza_clave

# --- privilegios: root, sudo sin contraseña o sudo con contraseña (en un terminal)
exige_maestra
PRIV="$(admin 'if [ "$(id -u)" = 0 ]; then echo root; elif ! command -v sudo >/dev/null 2>&1; then echo sin_sudo;
  elif ! id -nG | tr " " "\n" | grep -qxE "sudo|wheel|admin"; then echo sin_grupo; elif sudo -n true 2>/dev/null; then echo sudo_n; else echo sudo_pide; fi' \
  2>/dev/null | tail -n 1 || true)"
case "$PRIV" in
  root|sudo_n|sudo_pide) ;;
  sin_grupo) echo "Aviso: $USUARIO no está en el grupo sudo (ni wheel/admin). Si sudo te rechaza, entra como root o añade tu usuario a sudo." ;;
  sin_sudo) echo "El servidor no tiene sudo y $USUARIO no es root. Entra como root (root@$HOST) o instala sudo (apt install sudo) y repite."; exit 1 ;;
  *) echo "No pude comprobar los permisos de $USUARIO en el servidor (se cortó la conexión)."; exit 1 ;;
esac
if [ "$PRIV" = sudo_pide ] || [ "$PRIV" = sin_grupo ]; then
  [ -t 0 ] && [ -t 1 ] || { echo "sudo te pedirá la contraseña y esto no se está ejecutando en un terminal: lánzalo en la app Terminal."; exit 1; }
fi

echo "-> copiando scripts a $DESTINO"
exige_maestra
REMOTO="$(admin 'mktemp -d /tmp/infobar-alta.XXXXXX' 2>/dev/null | tail -n 1 || true)"
# lo que sigue se usa con sudo y rm -rf: solo una ruta de mktemp exacta
[[ "$REMOTO" =~ ^/tmp/infobar-alta\.[A-Za-z0-9]{6}$ ]] || { echo "respuesta inesperada del servidor al crear la carpeta temporal"; REMOTO=""; exit 1; }
exige_maestra
if ! scp -q -o "ControlPath=$SOCK" -o ControlMaster=no -P "$PUERTO" ${OPC_CLAVE[@]+"${OPC_CLAVE[@]}"} "$AQUI/instalar-en-servidor.sh" "$AQUI/recoger.sh" "$AQUI/recoger-root.sh" "$DESTINO:$REMOTO/"; then
  echo "No se pudieron copiar los scripts (¿disco lleno o /tmp de solo lectura en el servidor?)."; exit 1
fi

ORDEN="bash $REMOTO/instalar-en-servidor.sh '$PUB'; rc=\$?; rm -rf $REMOTO; exit \$rc"
exige_maestra
RC=0
case "$PRIV" in
  root) echo "-> instalando"; admin "$ORDEN" || RC=$? ;;
  sudo_n) echo "-> instalando (sudo sin contraseña)"; admin "sudo -n $ORDEN" || RC=$? ;;
  *) echo "-> instalando (sudo te pedirá TU contraseña de $USUARIO en el servidor, una vez)"
     ssh -t "${OPC_MAESTRA[@]}" -- "$DESTINO" "sudo -p '[sudo] contraseña de %p en el servidor: ' $ORDEN" || RC=$? ;;
esac
REMOTO=""                                         # la propia orden borra su carpeta
if [ "$RC" -ne 0 ]; then
  echo ""
  if [ "$RC" = 255 ]; then
    echo "Se cortó la conexión durante la instalación. Repite el script (es idempotente)."
  else
    echo "La instalación no terminó bien (código $RC). Si sudo rechazó tu contraseña o dice «not in the sudoers file»,"
    echo "tu usuario no puede usar sudo: entra como root o pide que lo añadan al grupo sudo. Si no, lee los mensajes de arriba."
  fi
  exit 1
fi

prueba_fallida() {
  echo ""
  echo "Se instaló, pero la prueba como infobar falló: revisa los avisos de arriba (¿AllowUsers/AllowGroups en sshd?"
  echo "¿otro puerto para usuarios normales?). El servidor no se ha añadido a la app."
  exit 1
}
echo "-> probando como infobar"
if [ "$SIN_REGISTRO" = 1 ]; then
  # sin Python no hay registro: se prueba con las opciones del monitor (sin el ~/.ssh/config del dueño, huella estricta)
  # la salida se guarda antes de buscar en ella: con pipefail, un grep -q que corta la tubería haría fallar a ssh (SIGPIPE)
  PRUEBA="$(ssh -F /dev/null -T -p "$PUERTO" -i "$CLAVE" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=yes \
       -o ProxyCommand=none -o ControlMaster=no -o ControlPath=none -- "infobar@$HOST" </dev/null || true)"
  grep -q '^### version' <<<"$PRUEBA" || prueba_fallida
fi

json_texto() {   # el nombre dentro de una cadena JSON: sin caracteres de control, con \ y " escapados
  local t; t="$(printf '%s' "$1" | tr -d '[:cntrl:]')"
  t="${t//\\/\\\\}"; t="${t//\"/\\\"}"
  printf '%s' "$t"
}
FICHA="{\"nombre\": \"$(json_texto "$NOMBRE")\", \"ssh\": \"infobar@$HOST\"}"
[ "$PUERTO" = 22 ] || FICHA="${FICHA%\}}, \"puerto\": $PUERTO}"
no_registrado() {
  echo "Lo instalado queda bien; solo falta añadirlo a la app. Hazlo en Ajustes -> Servidores (Dar de alta, con «${NOMBRE}»"
  echo "y $DESTINO: detecta que ya está instalado), o con:"
  echo "  $(linea_registro "$NOMBRE")"
  echo "o a mano en servidores.json:  $FICHA"
}
echo ""
if [ "$SIN_REGISTRO" = 1 ]; then
  echo "La lectura funciona."; no_registrado; exit 1
fi
# --probar: la app lo lee con su misma orden (la del monitor) y solo entonces lo guarda
RC=0; registro --probar "$NOMBRE" "$HOST" "$PUERTO" || RC=$?
case "$RC" in
  5) prueba_fallida ;;
  0) echo "OK. Servidor «${NOMBRE}» añadido a la app: la barra lo leerá en el próximo ciclo (unos 5 minutos)." ;;
  4) echo "OK. «${NOMBRE}» ya estaba en la app: scripts actualizados, la barra usará la versión nueva en la próxima lectura." ;;
  3) echo "La lectura funciona, pero otro servidor se llama ya «${NOMBRE}» (se dio de alta mientras tanto). Añádelo con otro nombre:"
     echo "  $(linea_registro OTRO_NOMBRE)"; exit 1 ;;
  *) echo "La lectura funciona, pero no pude guardarlo en la lista de la app."; no_registrado; exit 1 ;;
esac
