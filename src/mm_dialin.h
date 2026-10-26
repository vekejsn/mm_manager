#ifndef MM_DIALIN_H_
#define MM_DIALIN_H_
/* Returns 0 for management (buffered bytes replayed), 1 for a handled
 * dial-in call, -1 for unclassified/failed calls. Serial and TCP; opt-in -j. */
int mm_dialin_route(mm_proto_t *proto, unsigned short port);
int mm_manager_is_running(void);
#endif
