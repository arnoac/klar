/* Runs a command and reports its peak resident memory on stderr. Used by run.py: measuring from Python
 * directly would count the Python parent's memory that a forked child holds until it calls exec. */
#include <stdio.h>
#include <sys/resource.h>
#include <sys/wait.h>
#include <unistd.h>
int main(int argc, char **argv) {
  if (argc < 2) return 2;
  pid_t p = fork();
  if (p == 0) { execvp(argv[1], argv + 1); _exit(127); }
  int st; struct rusage ru;
  wait4(p, &st, 0, &ru);
  fprintf(stderr, "MAXRSS_KB %ld\n", ru.ru_maxrss);
  return WIFEXITED(st) ? WEXITSTATUS(st) : 1;
}
