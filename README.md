# README.md
# UFRA+ Rede Social Descentralizada

Uma rede social descentralizada onde vários servidores podem colaborar para manter a rede funcionando.

## Características

- Interface de usuário semelhante ao Twitter
- Sistema de autenticação de usuários
- Publicação de mensagens com suporte a imagens
- Sistema de curtidas e comentários
- Persistência de dados com SQLite
- Arquitetura descentralizada - múltiplos servidores podem se conectar
- Sincronização automática entre servidores
- Distribuição de carga entre servidores

## Requisitos

- Python 3.7+
- Bibliotecas listadas em `requirements.txt`

## Instalação

1. Clone o repositório ou baixe os arquivos

2. Instale as dependências:
```bash
pip install -r requirements.txt
```

3. Execute o servidor:
```bash
uvicorn main:app --reload
```

Por padrão, o servidor será iniciado na porta 8000.

## Iniciando servidores adicionais

Para iniciar servidores adicionais em diferentes portas:

```bash
python main.py 8001
python main.py 8002
```

Cada servidor adicional se registrará automaticamente no servidor principal (porta 8000) e começará a sincronizar os dados.

## Estrutura do Projeto

- `main.py` - Arquivo principal do backend com a API FastAPI
- `index.html` - Interface do usuário
- `uploads/` - Diretório onde as imagens enviadas são armazenadas
- `ufraplus.db` - Banco de dados SQLite onde os dados são persistidos

## Como funciona a sincronização

- Cada servidor registra outros servidores conhecidos
- Os dados são sincronizados periodicamente entre os servidores
- Ações como postar, curtir ou comentar são transmitidas para todos os servidores conhecidos
- Se um servidor ficar offline, os dados serão sincronizados quando ele voltar
- Servidores verificam periodicamente quais servidores estão ativos

## Uso

1. Abra o navegador e acesse `http://localhost:8000`
2. Crie uma conta ou faça login
3. Comece a postar, curtir e comentar!

## Expandindo a Rede

Para adicionar um servidor à rede em outra máquina, execute o servidor com a URL completa:

```bash
MY_SERVER_URL=http://meu-ip:porta python main.py porta
```

E registre-o manualmente em pelo menos um servidor existente na rede:

```bash
curl -X POST -H "Content-Type: application/json" -d '{"server_url":"http://meu-ip:porta"}' http://servidor-existente/register_server
```

---

Desenvolvido como um projeto de rede social descentralizada.