There's three legs to the Written Realms stool: the Forge (long term data store), the Nexuses (real-time game engine) and Herald (the frontend).

# The Forge
The long term data store is called the Forge. It is a Django app server and a Postgres DB where all of the user account info, world templates, player saved data, and any kind of long term progress is persisted.

We use Django Rest Framework to serve the various endpoints, with JWT as the authentication mechanism.

The Forge has access to its nexuses, including to the Redis DB within a Nexus. This is used to spin up worlds initially and then with loaders.

# Nexuses
When a player connects to the game, a websocket connection is opened to a Nexus, which is a container running Torando, ZeroMQ, Redis, and a lot custom python code built on top of a custom ORM.

## Pulsar
The key component of the Nexus is a PUB socket called Pulsar which polls Redis every 0.01s, pops the older record off a 'timings' zrange, resolves it and then sends out the resulting messages to the Game ROUTER socket which has subbed to the Pulsar socket.

Pulsar is theoretically the only component which ever does write operations to the game state in Redis. This means that within each Nexus, all instructions that lead to changes in the world are executed in order by this one process. Back in the days we used to try clever redis transactional optimistic locking tricks but found that to be an debuggable mess and switched to this single process architecture. This prevents a whole host of race conditions that plagued the old system.

Pulsar is the workhorse of the system. It is doing all of the heavy lifting of processing all player, mob and room commands and resolving them into state changes and update messages.

## Beat & Tic
The Game ROUTER binds to two other PUB sockets, 'beat' and 'tic'. Beat is polled every 2 seconds while Tic is polled every 15 seconds. They both read the game state in Redis and then publish messages to the Game hub. They also queue instructions to the timings queue that Pulsar consumes.

Quick note that the Tic does have one process which is indirectly responsible for game state changes - the Loader Tic. This pings the Forge to run the loaders  which do create new mobs in the Redis DB.

## Background & Mobs
The Background and Mob nodes are both DEALER and connect to the Game ROUTER. They receive messages routed to them by the Game hub and respond with their own messages.

The Background process is in charge of carrying out messages that require endpoint calls to the Forge, some of which could take a while to run. Examples of this are saving player data, completing quests, loading items or mobs from the command line, entering / exiting instances, crafting gear.

The Mob process receives all messages that are addressed to mobs and respond with their own messages via the Reaction system.

## Websockets
Each websocket connection is a DEALER socket connecting to the Game ROUTER, sending player commands and receiving game updates. The websockets are handled by a Tornado server, which integrates with ZeroMQ to forward the messages onto the framework.

## Game
The Game ROUTER sits in the middle of the entire system, connected to all the other sockets. It processes messages received from the Websocket nodes, adds Timing Queue entries to be picked up by Pulsar later and routes messages to the Background, Mob and Websocket nodes based on the message addresses. It also receives messages from Pulsar, Beat and Tic and routes those too.

# Frontend
The frontend is built in Vue 3 with Vuex and communicates with both the Forge for all of the UI REST calls, and with the Nexus for the real-time gameplay via Websockets.

# Infrastructure
In production, Written Realms is deployed on a 4 node Kubernetes cluster. One of the nodes runs the Postgres DB, another runs the Edeus Nexus (the nexus that our flagship multiplayer world runs on), and the other 2 nodes are shared between the Forge (gunicorn and celery processes) and the 3 other Nexuses.
