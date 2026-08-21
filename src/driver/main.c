#include "c167cc/ast.h"
#include "c167cc/ir.h"
#include "c167cc/c167_target.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

extern TranslationUnit *g_tu;
extern const char *g_input_filename;
extern FILE *yyin;
extern int yyparse(void);

static void usage(const char *prog) {
    fprintf(stderr,
        "usage: %s [options] <input.c>\n"
        "options:\n"
        "  -o <file>      write output to <file> (default: <input>.asm)\n"
        "  -S             generate assembly (default and only supported mode)\n"
        "  --dump-ast     print the AST and exit\n"
        "  --dump-ir      print the IR and exit\n"
        "  --dump-asm     print the generated assembly to stdout\n"
        "  --verbose      print progress information to stderr\n"
        "  -h, --help     show this message\n",
        prog);
}

int main(int argc, char **argv) {
    const char *input = NULL;
    const char *output = NULL;
    int dump_ast = 0, dump_ir = 0, dump_asm = 0, verbose = 0;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-o") == 0 && i + 1 < argc) { output = argv[++i]; }
        else if (strcmp(argv[i], "-S") == 0) { /* default mode, accepted for CLI compatibility */ }
        else if (strcmp(argv[i], "--dump-ast") == 0) dump_ast = 1;
        else if (strcmp(argv[i], "--dump-ir") == 0) dump_ir = 1;
        else if (strcmp(argv[i], "--dump-asm") == 0) dump_asm = 1;
        else if (strcmp(argv[i], "--verbose") == 0) verbose = 1;
        else if (strcmp(argv[i], "-h") == 0 || strcmp(argv[i], "--help") == 0) { usage(argv[0]); return 0; }
        else if (argv[i][0] == '-' && strcmp(argv[i], "-") != 0) { fprintf(stderr, "unknown option: %s\n", argv[i]); usage(argv[0]); return 1; }
        else input = argv[i];
    }

    if (!input) { usage(argv[0]); return 1; }

    yyin = fopen(input, "r");
    if (!yyin) { fprintf(stderr, "%s: cannot open '%s'\n", argv[0], input); return 1; }
    g_input_filename = input;
    g_tu = tu_new(input);

    if (verbose) fprintf(stderr, "[c167cc] parsing %s\n", input);
    yyparse();
    fclose(yyin);

    if (dump_ast) { ast_dump(g_tu); return 0; }

    if (verbose) fprintf(stderr, "[c167cc] building IR\n");
    IrModule *mod = ir_build(g_tu);

    if (verbose) fprintf(stderr, "[c167cc] running optimizer\n");
    ir_optimize(mod);

    if (dump_ir) { ir_dump(mod); return 0; }

    if (verbose) fprintf(stderr, "[c167cc] generating C167 assembly\n");
    AsmProgram *prog = c167_codegen(mod);

    if (dump_asm) { c167_print(prog, stdout); return 0; }

    char default_out[4096];
    if (!output) {
        snprintf(default_out, sizeof(default_out), "%s", input);
        char *dot = strrchr(default_out, '.');
        if (dot && strcmp(dot, ".c") == 0) strcpy(dot, ".asm");
        else strcat(default_out, ".asm");
        output = default_out;
    }

    FILE *out = strcmp(output, "-") == 0 ? stdout : fopen(output, "w");
    if (!out) { fprintf(stderr, "%s: cannot open '%s' for writing\n", argv[0], output); return 1; }
    c167_print(prog, out);
    if (out != stdout) fclose(out);

    if (verbose) fprintf(stderr, "[c167cc] wrote %s\n", output);
    return 0;
}
