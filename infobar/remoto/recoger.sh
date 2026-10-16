#!/bin/bash
# infobar: lectura del estado del servidor. Se instala en /usr/local/bin/infobar-recoger (root:root
# 0755) y es lo UNICO que puede ejecutar la clave SSH de infobar (command= en authorized_keys).
# Solo lee. Cada comando con tiempo maximo y sin stdin; lo que falle sale vacio.
# Salida: secciones "### <nombre>" que el Mac interpreta (las desconocidas se ignoran).
# Las secciones con marco ("n <lineas>" al principio y "fin <lineas> <codigo>" al final) solo se dan por
# completas si traen el cierre; el script termina con "### fin".
export LC_ALL=C PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
# Presupuesto de tiempo (el Mac corta a los 20 s contando la conexion): ningun comando arranca pasados 6 s, el
# bloque de intentos y el sudo reciben solo lo que queda y el vigilante cierra a los 14 s.
INICIO=$(date +%s)
resta() { echo $(( $1 - ($(date +%s) - INICIO) )); }
t() { [ "$(resta 6)" -le 0 ] && return 1; timeout 5 "$@" </dev/null 2>/dev/null; }
( sleep 14; kill -TERM -- "-$$" 2>/dev/null; kill -TERM $$ 2>/dev/null ) </dev/null >/dev/null 2>&1 &
VIGILANTE=$!
trap 'kill $VIGILANTE 2>/dev/null' EXIT

echo "### version"
echo 3                                             # 3 = 2 + las secciones opcionales del final (correo, espera)

echo "### sistema"
t cat /proc/loadavg
t nproc
t cut -d' ' -f1 /proc/uptime

echo "### memoria"
t free -b | awk '/^Mem:/{print $2, $7}'            # total disponible

echo "### disco"
t df -B1 --output=size,used / | tail -n 1

echo "### sesiones"
t who                                              # la sesion de infobar no tiene tty: no sale aqui

echo "### puertos"
t ss -tlnH | awk '{print $4}' | grep -vE '^(127\.|\[::1\]|::1)' | sed 's/.*://' | sort -un | tr '\n' ' '
echo

echo "### intentos"
# Intentos de acceso SSH rechazados. Solo cuentan las lineas que escribio sshd de verdad: se filtra por la
# unidad de systemd (campo que pone journald; `logger -t sshd` no lo falsea, a diferencia de -t y de _COMM)
# y las IPs solo se dan por fiables si las sesiones de usuario no viven en el cgroup de sshd (aislamiento).
# Las reglas se aplican al MENSAJE (lo que va tras "ident[pid]: "), no a la linea entera: el nombre de usuario
# lo elige el atacante y no puede hacerse pasar por otra linea. Los cubos son horas naturales (hora de epoca);
# "-" = el journal no llega a esa hora (desconocido, no 0). Se lee de la mas nueva a la mas vieja: si se corta
# por tiempo se pierde lo antiguo, no lo que esta pasando ahora.
UNIDADES="_SYSTEMD_UNIT=ssh.service _SYSTEMD_UNIT=sshd.service"
ahora=$(date +%s)
ini=$(( (ahora / 3600 - 24) * 3600 ))
cuerpo=$(
  echo "ahora $ahora"
  if [ "$(resta 4)" -le 0 ]; then
    echo "fuente error"
  elif [ -n "$(timeout 2 journalctl -q -n 1 -o cat $UNIDADES </dev/null 2>/dev/null)" ]; then
    echo "fuente unit"
    grep -qE 'session-[0-9]+\.scope' /proc/self/cgroup 2>/dev/null && echo "aislamiento si" || echo "aislamiento no"
    cob=$(timeout 2 journalctl -q -o short-unix </dev/null 2>/dev/null | head -n 1 | cut -d. -f1 | tr -cd '0-9')
    echo "cobertura ${cob:-0}"
    lim=$(resta 11); [ "$lim" -gt 5 ] && lim=5; [ "$lim" -lt 1 ] && lim=1
    timeout "$lim" journalctl -q -r -o short-unix --since "@$ini" --until "@$ahora" $UNIDADES </dev/null 2>/dev/null | cut -b -400 | awk -v ahora="$ahora" -v ini="$ini" -v cob="${cob:-0}" '
      {
        ts = int($1)
        if (ts > ahora || ts < ini) next
        msg = $0; sub(/^[^ ]+ [^ ]+ [^ ]+ /, "", msg)
        # un intento deja varias lineas (Invalid user, Disconnected from invalid user...): se cuenta solo la linea
        # canonica de cada uno para no multiplicar
        if (msg !~ /^(Invalid user |Connection closed by authenticating user |Failed password for |error: maximum authentication attempts|maximum authentication attempts)/) next
        if (msg ~ /^Failed password for invalid user /) next
        # la IP es el campo anterior al ULTIMO "port": el usuario (que elige el atacante) va antes
        ip = ""
        for (i = NF; i > 1; i--) if ($i == "port" && $(i + 1) ~ /^[0-9]+$/) {
          cand = $(i - 1)
          if (cand ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ || cand ~ /^[0-9a-fA-F:]+:[0-9a-fA-F:]+$/) ip = cand
          break
        }
        if (ip == "") next
        h[int(ts / 3600)]++
        if (ahora - ts <= 86400) { total++; n[ip]++ }
        if (ahora - ts <= 3600) hora++
      }
      END {
        print "total", total + 0; print "hora", hora + 0
        act = int(ahora / 3600)
        for (k = act - 24; k <= act; k++) { if (cob == 0 || k * 3600 < cob) print "h", k, "-"; else print "h", k, h[k] + 0 }
        for (c = 0; c < 12; c++) {
          mejor = ""; max = 0
          for (x in n) if (n[x] > max) { max = n[x]; mejor = x }
          if (mejor == "") break
          print "ip", mejor, max; delete n[mejor]
        }
      }'
    rc=${PIPESTATUS[0]}
    # 0 = bien, 1 = nada que mostrar en algunas versiones; cualquier otro (124 = tiempo) = lectura incompleta
    if [ "$rc" = 0 ] || [ "$rc" = 1 ]; then echo "parcial no"; else echo "parcial si"; fi
  else
    echo "fuente ninguna"
  fi
)
cuerpo=$(printf '%s\n' "$cuerpo" | head -n 80)
nlin=$(printf '%s\n' "$cuerpo" | grep -c '')
echo "n $nlin"; printf '%s\n' "$cuerpo"; echo "fin $nlin 0"

echo "### actualizaciones"
t apt list --upgradable 2>/dev/null | grep -c upgradable
[ -f /var/run/reboot-required ] && echo reinicio || echo sin-reinicio

echo "### bastionado"
# Comprobaciones de buenas practicas que no necesitan root (clave valor). El Mac las convierte en
# la lista de "que falta"; las que necesitan root van en recoger-root.
au=$(t cat /etc/apt/apt.conf.d/20auto-upgrades)
echo "auto_upgrades $(echo "$au" | grep -q 'Unattended-Upgrade "1"' && echo si || echo no)"
echo "unattended_instalado $(t dpkg-query -W -f='${Status}' unattended-upgrades | grep -q 'install ok installed' && echo si || echo no)"
echo "parches_seguridad $(t apt list --upgradable 2>/dev/null | grep -c -- '-security')"
echo "uid0_extra $(t awk -F: '$3==0 && $1!="root"' /etc/passwd | wc -l)"
echo "hora_sincronizada $(t timedatectl show -p NTPSynchronized --value)"

# docker y fail2ban necesitan root: un unico programa sin argumentos, permitido en sudoers. Recibe solo el tiempo que queda
# (si no hay para su peor caso, sus secciones llegan sin cierre y el Mac las descarta: «sin dato», no ceros).
if [ -x /usr/local/lib/infobar/recoger-root ]; then
  restante=$(resta 13)
  [ "$restante" -ge 4 ] && timeout -k 1 "$restante" sudo -n /usr/local/lib/infobar/recoger-root </dev/null 2>/dev/null
fi

# Lo opcional va al FINAL, con el tiempo que sobre, para no quitarselo a ninguna comprobacion de arriba:
# - correo: servidor de correo instalado en el sistema (no en docker); sus puertos 25/465/587/993 son legitimos.
# - espera: cuantos paquetes instalaria `apt upgrade` de verdad (simulado, sin root; tarda 1-2 s). El resto de los
#   «upgradable» estan en espera: actualizaciones por fases de Ubuntu o retenidos (p. ej. un salto de version de docker).
# Sin tiempo, la seccion no sale y el Mac se queda con lo de siempre.
# Todo acaba antes del segundo 13 (el vigilante corta a los 14): sin «### fin» la lectura entera se da por incompleta.
if [ "$(resta 9)" -ge 2 ]; then
  echo "### correo"
  UNIDADES_CORREO="postfix dovecot exim4 opensmtpd"
  # una sola llamada: systemctl responde una linea por unidad, en el mismo orden
  timeout -k 1 2 systemctl is-active $UNIDADES_CORREO </dev/null 2>/dev/null \
    | paste -d' ' <(printf '%s\n' $UNIDADES_CORREO) - | awk '$2 == "active" {print $1}'
fi
lim=$(resta 12)
# --with-new-pkgs: lo mismo que `apt upgrade` (un kernel nuevo no queda «retenido»)
if [ "$lim" -ge 3 ] && sim=$(timeout -k 1 "$lim" apt-get -s -o Debug::NoLocking=1 --with-new-pkgs upgrade </dev/null 2>/dev/null); then
  echo "### espera"
  echo "instalables $(printf '%s\n' "$sim" | grep -c '^Inst ')"
fi

echo "### fin"
