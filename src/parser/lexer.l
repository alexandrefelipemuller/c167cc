%{
#include <stdlib.h>
#include <string.h>
#include "c167cc/ast.h"
#include "c167cc/parse_lists.h"
#include "parser.tab.h"

const char *g_input_filename = "<stdin>";
%}

%option noyywrap
%option yylineno

DIGIT   [0-9]
HEX     [0-9a-fA-F]
ID      [A-Za-z_][A-Za-z0-9_]*

%%

"//".*                          { /* line comment */ }
"/*"([^*]|\*+[^*/])*\*+"/"       { /* block comment */ }
[ \t\r\n]+                      { /* whitespace */ }

"void"                          { return KW_VOID; }
"char"                          { return KW_CHAR; }
"signed"                        { return KW_SIGNED; }
"unsigned"                      { return KW_UNSIGNED; }
"int8_t"                        { return KW_I8; }
"uint8_t"                       { return KW_U8; }
"int16_t"                       { return KW_I16; }
"uint16_t"                      { return KW_U16; }
"int32_t"                       { return KW_I32; }
"uint32_t"                      { return KW_U32; }
"if"                            { return KW_IF; }
"else"                          { return KW_ELSE; }
"while"                         { return KW_WHILE; }
"for"                           { return KW_FOR; }
"return"                        { return KW_RETURN; }
"break"                         { return KW_BREAK; }
"continue"                      { return KW_CONTINUE; }
"switch"                        { return KW_SWITCH; }
"case"                          { return KW_CASE; }
"default"                       { return KW_DEFAULT; }
"volatile"                      { return KW_VOLATILE; }
"const"                         { return KW_CONST; }
"enum"                          { return KW_ENUM; }
"struct"                        { return KW_STRUCT; }
"union"                          { return KW_UNION; }

"@ram"                          { return AT_RAM; }
"@rom"                          { return AT_ROM; }
"@interrupt"                    { return AT_INTERRUPT; }

"=="                            { return TOK_EQ; }
"!="                             { return TOK_NE; }
"<="                            { return TOK_LE; }
">="                            { return TOK_GE; }
"&&"                            { return TOK_ANDAND; }
"||"                            { return TOK_OROR; }
"<<"                            { return TOK_SHL; }
">>"                            { return TOK_SHR; }
"++"                             { return OP_INC; }
"--"                             { return OP_DEC; }
"+="                             { return OP_ADD_ASSIGN; }
"-="                             { return OP_SUB_ASSIGN; }
"*="                             { return OP_MUL_ASSIGN; }
"/="                             { return OP_DIV_ASSIGN; }
"&="                             { return OP_AND_ASSIGN; }
"|="                             { return OP_OR_ASSIGN; }
"^="                             { return OP_XOR_ASSIGN; }
"->"                             { return TOK_ARROW; }

0[xX]{HEX}+                     { yylval.ival = strtol(yytext, NULL, 16); return INT_LIT; }
{DIGIT}+[uUlL]*                 { yylval.ival = strtol(yytext, NULL, 10); return INT_LIT; }

{ID}                            { yylval.str = strdup(yytext); return IDENT; }

.                                { return yytext[0]; }

%%
