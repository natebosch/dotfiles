wiki() { dig +short txt $1.wp.dg.cx; }

calc(){ awk "BEGIN{ print $* }" ;}

ipaddr() { 
    echo "Internal:"
    ifconfig $1 | grep "inet addr" | gawk -F: '{print $2}' | gawk '{print $1}' 
    echo "External:"
    wget www.whatismyip.com/automation/n09230945.asp -O - -o /dev/null
    echo
}

port() {
    port=$1
    procinfo=$(netstat --numeric-ports -nlp 2> /dev/null | grep ^tcp | grep -w ${port} | tail -n 1 | awk '{print $7}')

    case "${procinfo}" in
    "")
      echo "No process listening on port ${port}"
      ;;
    "-")
      echo "Process is running on ${port}, but current user does not have rights to see process information."
      ;;
    *)
      echo "${procinfo} is running on port ${port}"
      ps -uwep ${procinfo%/*}
      ;;
    esac
}

checkdns() {
    HOSTNAME= hostname
    echo "Hostname: $HOSTNAME"
    REALIP=`ifconfig $1 | grep "inet addr" | gawk -F: '{print $2}' | gawk '{print $1}'`
    echo "Actual IP: $REALIP"
    IP=`dig +short $HOSTNAME`
    echo "Dig IP: $IP"
    DIGHOST=`dig +short -x "$IP"`
    echo "Dig hostname: $DIGHOST"
}

watchfile() {
    watch -n 30 -d "ls -l $1 | awk '{print \$5}' && ls -lh $1 | awk '{print \$5}' && df -h"
}

say() { if [[ "${1}" =~ -[a-z]{2} ]]; then local lang=${1#-}; local text="${*#$1}"; else local lang=${LANG%_*}; local text="$*";fi; mplayer "http://translate.google.com/translate_tts?ie=UTF-8&tl=${lang}&q=${text}" &> /dev/null ; }

project () {
  local project_name=$1

  mkdir -p ~/projects
  cd ~/projects
  if [[ -d $project_name ]]; then
    cd $project_name/google3
    echo 'Project already exists'
  else
    echo "Starting project $project_name"
    mkdir $project_name
    cd $project_name
    if type start_project > /dev/null; then
      start_project
    else
      git init
    fi
  fi
  s $project_name
}
compdef '_arguments "1:project name:($(ls ~/projects))"' project

